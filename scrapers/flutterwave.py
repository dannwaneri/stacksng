"""Flutterwave docs scraper for the NaijaCode corpus.

Flutterwave runs on ReadMe.com. Rather than scrape rendered HTML, we pull the
embedded #ssr-props JSON, which gives us:
  - sidebars.docs   : the guide tree (slug + title + category)
  - sidebars.refs   : the reference tree (operation slugs grouped by tag)
  - doc.body        : raw markdown for any guide page
  - oasDefinition   : full OpenAPI spec for all endpoints

Strategy:
  - One chunk per guide page (markdown body)
  - One chunk per (path, method) operation, formatted from the OAS spec
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from bs4 import BeautifulSoup

BASE = "https://developer.flutterwave.com"
GUIDES_SEED = "https://developer.flutterwave.com/docs/getting-started"
REF_SEED = "https://developer.flutterwave.com/reference/customers_list"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_S = 0.8
TIMEOUT_S = 30
RATE_LIMIT_BACKOFF_S = 20
MAX_RETRIES_429 = 2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "corpus.db"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"
SOURCE_LABEL = "flutterwave"


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _fetch_once(url: str) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise FetchError("curl not found on PATH")
    cmd = [
        curl,
        "-sS",
        "-L",
        "--compressed",
        "-o", "-",
        "-w", "\n__HTTP_STATUS__:%{http_code}",
        "--max-time", str(TIMEOUT_S),
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FetchError(f"curl exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    body = proc.stdout
    status: int | None = None
    marker = body.rfind("\n__HTTP_STATUS__:")
    if marker != -1:
        try:
            status = int(body[marker + len("\n__HTTP_STATUS__:"):].strip())
        except ValueError:
            status = None
        body = body[:marker]
    if status is not None and status >= 400:
        raise FetchError(f"HTTP {status}", status=status)
    return body


def fetch(url: str) -> str:
    for attempt in range(MAX_RETRIES_429 + 1):
        try:
            return _fetch_once(url)
        except FetchError as e:
            if e.status == 429 and attempt < MAX_RETRIES_429:
                wait = RATE_LIMIT_BACKOFF_S * (attempt + 1)
                print(f"  rate-limited (429), backing off {wait}s before retry...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise FetchError("retries exhausted")


def extract_ssr_props(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="ssr-props")
    if tag is None:
        raise FetchError("no #ssr-props script tag found")
    raw = tag.string or tag.text or ""
    if not raw.strip():
        raise FetchError("#ssr-props is empty")
    return json.loads(raw)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "general"


def iter_sidebar(items: list[dict[str, Any]], inherited_category: str = "") -> Iterator[tuple[str, str, str]]:
    """Yield (slug, title, category) for every leaf in a ReadMe sidebar tree.

    Top-level items become categories; their descendants inherit that category.
    """
    for item in items:
        slug = item.get("slug")
        title = item.get("title") or item.get("name") or slug or ""
        children = item.get("pages") or item.get("children") or []
        if not inherited_category:
            category = slugify(title)
            if slug:
                yield slug, title, category
            if children:
                yield from iter_sidebar(children, category)
        else:
            if slug:
                yield slug, title, inherited_category
            if children:
                yield from iter_sidebar(children, inherited_category)


def clean_markdown(md: str) -> str:
    md = re.sub(r"\r\n?", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def resolve_ref(spec: dict[str, Any], node: Any, seen: set[str] | None = None) -> Any:
    """Follow $ref pointers in the OAS spec. Returns the dereferenced node.

    Tracks visited refs per call chain to avoid infinite recursion on cyclic schemas.
    """
    if seen is None:
        seen = set()
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not ref:
        return node
    if ref in seen or not ref.startswith("#/"):
        return {"description": f"(circular or external ref: {ref})"}
    seen = seen | {ref}
    parts = ref[2:].split("/")
    target: Any = spec
    for p in parts:
        if not isinstance(target, dict):
            return {"description": f"(unresolved ref: {ref})"}
        target = target.get(p)
        if target is None:
            return {"description": f"(unresolved ref: {ref})"}
    return resolve_ref(spec, target, seen)


def render_operation(path: str, method: str, op: dict[str, Any], spec: dict[str, Any]) -> tuple[str, str]:
    """Format one OAS operation as a markdown chunk. Returns (category, content)."""
    method_u = method.upper()
    summary = op.get("summary") or ""
    description = op.get("description") or ""
    tags = op.get("tags") or []
    category = slugify(tags[0]) if tags else "api"
    operation_id = op.get("operationId") or ""

    lines: list[str] = []
    if summary:
        lines.append(f"# {summary}")
    lines.append(f"**{method_u}** `{path}`")
    if operation_id:
        lines.append(f"Operation ID: `{operation_id}`")
    if description.strip():
        lines.append("")
        lines.append(description.strip())

    params = op.get("parameters") or []
    if params:
        lines.append("")
        lines.append("## Parameters")
        for raw_p in params:
            p = resolve_ref(spec, raw_p)
            name = p.get("name", "")
            loc = p.get("in", "")
            required = " (required)" if p.get("required") else ""
            schema = resolve_ref(spec, p.get("schema") or {})
            ptype = schema.get("type") or ""
            pdesc = (p.get("description") or "").strip().splitlines()
            pdesc_first = pdesc[0] if pdesc else ""
            lines.append(f"- `{name}` [{loc}{', ' + ptype if ptype else ''}]{required} — {pdesc_first}")

    request_body = op.get("requestBody")
    if isinstance(request_body, dict):
        rb = resolve_ref(spec, request_body)
        content = rb.get("content") or {}
        json_schema = (content.get("application/json") or {}).get("schema")
        if json_schema:
            lines.append("")
            lines.append("## Request Body (application/json)")
            lines.append(summarize_schema(json_schema, spec))

    responses = op.get("responses") or {}
    success = next((c for c in responses if str(c).startswith("2")), None)
    if success and isinstance(responses[success], dict):
        resp = resolve_ref(spec, responses[success])
        desc = resp.get("description") or ""
        lines.append("")
        lines.append(f"## Response {success}")
        if desc:
            lines.append(desc.strip())
        content = resp.get("content") or {}
        json_schema = (content.get("application/json") or {}).get("schema")
        if json_schema:
            lines.append(summarize_schema(json_schema, spec))

    body = "\n".join(lines).strip()
    cite_url = f"{BASE}/reference/{operation_id}" if operation_id else f"{BASE}/reference/"
    content = f"Source: {cite_url}\nSection: {method_u} {path}\n\n{body}"
    return category, content


def summarize_schema(schema: Any, spec: dict[str, Any], depth: int = 0, max_depth: int = 3) -> str:
    """Render a JSON schema as compact bullet markdown. Resolves $ref via spec; caps recursion."""
    if depth > max_depth:
        return "  " * depth + "- ..."
    schema = resolve_ref(spec, schema)
    if not isinstance(schema, dict):
        return ""

    t = schema.get("type")
    if t == "object" or schema.get("properties"):
        out: list[str] = []
        required = set(schema.get("required") or [])
        for name, raw_prop in (schema.get("properties") or {}).items():
            prop = resolve_ref(spec, raw_prop)
            ptype = prop.get("type") or ("object" if prop.get("properties") else "any")
            req_mark = " (required)" if name in required else ""
            desc = (prop.get("description") or "").strip().splitlines()
            desc_first = desc[0] if desc else ""
            example = prop.get("example")
            ex_str = f" e.g. `{example}`" if example not in (None, "") else ""
            out.append("  " * depth + f"- `{name}` [{ptype}]{req_mark} — {desc_first}{ex_str}")
            if ptype == "object" and prop.get("properties"):
                out.append(summarize_schema(prop, spec, depth + 1, max_depth))
            elif ptype == "array" and isinstance(prop.get("items"), dict):
                items = resolve_ref(spec, prop["items"])
                if items.get("properties"):
                    out.append("  " * (depth + 1) + "- items:")
                    out.append(summarize_schema(items, spec, depth + 2, max_depth))
        return "\n".join(out)
    if t == "array" and isinstance(schema.get("items"), dict):
        return summarize_schema(schema["items"], spec, depth, max_depth)
    return "  " * depth + f"- ({t or 'value'})"


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    else:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS corpus ("
            "id INTEGER PRIMARY KEY, source TEXT, category TEXT, content TEXT);"
        )
    return conn


def scrape_guides(conn: sqlite3.Connection, guides_props: dict[str, Any]) -> int:
    sidebar = guides_props.get("sidebars", {}).get("docs", [])
    guides = list(iter_sidebar(sidebar))
    print(f"[flutterwave] {len(guides)} guides discovered")

    total = 0
    seed_slug = (guides_props.get("doc") or {}).get("slug")
    for i, (slug, title, category) in enumerate(guides, 1):
        url = f"{BASE}/docs/{slug}"
        try:
            if slug == seed_slug:
                props = guides_props
            else:
                html = fetch(url)
                props = extract_ssr_props(html)
        except FetchError as e:
            if e.status == 404:
                print(f"  [{i}/{len(guides)}] skip {slug}: nav-only (404)")
            else:
                print(f"  [{i}/{len(guides)}] FAIL {slug}: {e}", file=sys.stderr)
            if slug != seed_slug:
                time.sleep(REQUEST_DELAY_S)
            continue
        except json.JSONDecodeError as e:
            print(f"  [{i}/{len(guides)}] FAIL {slug}: {e}", file=sys.stderr)
            if slug != seed_slug:
                time.sleep(REQUEST_DELAY_S)
            continue

        doc = props.get("doc") or {}
        body = clean_markdown(doc.get("body") or "")
        excerpt = (doc.get("excerpt") or "").strip()
        if not body and not excerpt:
            print(f"  [{i}/{len(guides)}] SKIP {slug}: empty body")
            if slug != seed_slug:
                time.sleep(REQUEST_DELAY_S)
            continue

        header_lines = [f"# {title}"]
        if excerpt:
            header_lines.append(excerpt)
        content_body = "\n\n".join(header_lines) + ("\n\n" + body if body else "")
        content = f"Source: {url}\nSection: {title}\n\n{content_body}"

        conn.execute(
            "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
            (SOURCE_LABEL, category, content),
        )
        conn.commit()
        total += 1
        print(f"  [{i}/{len(guides)}] {category}/{slug}: {len(content)} chars")

        if slug != seed_slug:
            time.sleep(REQUEST_DELAY_S)
    return total


def scrape_reference(conn: sqlite3.Connection, ref_props: dict[str, Any]) -> int:
    oas = ref_props.get("oasDefinition") or {}
    paths = oas.get("paths") or {}
    print(f"[flutterwave] {len(paths)} OAS paths in spec")

    total = 0
    for path, ops in paths.items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            if not isinstance(op, dict):
                continue
            category, content = render_operation(path, method, op, oas)
            conn.execute(
                "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                (SOURCE_LABEL, category, content),
            )
            total += 1
    conn.commit()
    print(f"[flutterwave] {total} OAS operations written")
    return total


def scrape(db_path: Path) -> int:
    conn = init_db(db_path)
    conn.execute("DELETE FROM corpus WHERE source = ?", (SOURCE_LABEL,))
    conn.commit()

    # Reference first — single fetch, yields ~70 chunks deterministically
    print(f"[flutterwave] seed reference: {REF_SEED}")
    ref_props = extract_ssr_props(fetch(REF_SEED))
    r_total = scrape_reference(conn, ref_props)

    # Guides second — many fetches, more likely to hit rate limits
    print(f"[flutterwave] seed guides: {GUIDES_SEED}")
    guides_props = extract_ssr_props(fetch(GUIDES_SEED))
    g_total = scrape_guides(conn, guides_props)

    total = g_total + r_total
    conn.close()
    print(f"[flutterwave] done: {total} chunks ({g_total} guides + {r_total} endpoints) -> {db_path}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Flutterwave docs + OAS")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    scrape(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
