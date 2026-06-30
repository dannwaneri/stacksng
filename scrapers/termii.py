"""Termii developer docs scraper for the NaijaCode corpus.

Termii is a Nigerian SMS/OTP/messaging provider. Docs at developers.termii.com.
Platform: custom Nuxt/Vue SPA — most deep API content is hydrated client-side,
not in the static HTML. But:
  1. Each section landing page (/messaging, /token, etc.) has a real
     server-rendered <article class="prose"> intro with h1/h2 sections.
  2. The site links a Postman collection JSON hosted on S3 — we pull that
     directly and render each request as a chunk.

Note: as of 2026, Termii's docs do NOT cover USSD despite the brand often
being grouped with low-connectivity tooling. They cover SMS, voice OTP,
in-app token, and number lookup. The corpus value here is SMS/OTP for
low-connectivity verification flows.
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
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

BASE = "https://developers.termii.com"
INDEX_URL = BASE + "/"
POSTMAN_URL = "https://termii.s3.us-west-1.amazonaws.com/upload/files/UozvGXj5czYEeY4OmE2f.json"

# Section slugs come from sidebar; hardcoding the known set is fine — they
# rarely change and the homepage already enumerates them.
SECTION_SLUGS = [
    "authentication",
    "messaging",
    "token",
    "insights",
    "events-and-reports",
    "libraries-and-plugins",
    "error",
    "eSIMs",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_S = 0.8
TIMEOUT_S = 30
SOURCE_LABEL = "termii"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "corpus.db"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

TEXT_TAGS = ("p", "pre", "code", "li", "h3", "h4", "h5", "table", "blockquote")


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def fetch(url: str) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise FetchError("curl not found on PATH")
    cmd = [
        curl, "-sS", "-L", "--compressed",
        "-o", "-",
        "-w", "\n__HTTP_STATUS__:%{http_code}",
        "--max-time", str(TIMEOUT_S),
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml,application/json,*/*;q=0.8",
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


def extract_section_chunks(url: str, html: str, slug: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article")
    if not isinstance(article, Tag):
        return []

    headings = article.find_all(["h1", "h2"])
    if not headings:
        return []

    chunks: list[str] = []
    for i, heading in enumerate(headings):
        next_heading = headings[i + 1] if i + 1 < len(headings) else None
        anchor = heading.get("id", "") or ""
        title = heading.get_text(" ", strip=True).strip()

        parts: list[str] = []
        for el in heading.find_all_next():
            if el is next_heading:
                break
            if not isinstance(el, Tag):
                continue
            if el.name not in TEXT_TAGS:
                continue
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            parts.append(text)

        body = "\n".join(parts)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if not body and not title:
            continue
        cite = f"{url}#{anchor}" if anchor else url
        chunks.append(f"Source: {cite}\nSection: {title}\n\n{body}".strip())
    return chunks


def render_postman_request(folder: str, name: str, request: dict[str, Any]) -> str:
    method = request.get("method", "")
    url = request.get("url")
    if isinstance(url, dict):
        raw_url = url.get("raw") or ""
    else:
        raw_url = url or ""

    lines: list[str] = [f"# {folder} — {name}"]
    if method or raw_url:
        lines.append(f"**{method}** `{raw_url}`")

    description = request.get("description")
    if isinstance(description, str) and description.strip():
        lines.append("")
        lines.append(description.strip())

    headers = request.get("header") or []
    if isinstance(headers, list) and headers:
        lines.append("")
        lines.append("## Headers")
        for h in headers:
            if isinstance(h, dict):
                key = h.get("key", "")
                value = h.get("value", "")
                desc = h.get("description", "")
                line = f"- `{key}: {value}`"
                if desc:
                    line += f" — {desc}"
                lines.append(line)

    body = request.get("body") or {}
    if isinstance(body, dict):
        mode = body.get("mode")
        if mode == "raw" and body.get("raw"):
            lines.append("")
            lines.append("## Request Body")
            lines.append("```")
            lines.append(body["raw"].strip())
            lines.append("```")
        elif mode in ("formdata", "urlencoded"):
            fields = body.get(mode) or []
            if fields:
                lines.append("")
                lines.append(f"## Request Body ({mode})")
                for f in fields:
                    if isinstance(f, dict):
                        lines.append(f"- `{f.get('key', '')}` — {f.get('description', '')}")

    return "\n".join(lines).strip()


def scrape_postman(conn: sqlite3.Connection) -> int:
    try:
        raw = fetch(POSTMAN_URL)
    except FetchError as e:
        print(f"[termii] postman fetch failed: {e}", file=sys.stderr)
        return 0
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[termii] postman parse failed: {e}", file=sys.stderr)
        return 0

    total = 0
    info = spec.get("info") or {}
    collection_name = info.get("name") or "Termii API"
    items = spec.get("item") or []

    def walk(items: list[Any], folder_path: list[str]) -> None:
        nonlocal total
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or "(unnamed)"
            sub = it.get("item")
            req = it.get("request")
            if isinstance(sub, list) and sub:
                walk(sub, folder_path + [name])
            elif isinstance(req, dict):
                folder = " / ".join(folder_path) if folder_path else collection_name
                body = render_postman_request(folder, name, req)
                category = slugify_category(folder_path[0] if folder_path else "api")
                content = (
                    f"Source: {POSTMAN_URL}#{slugify_category(name)}\n"
                    f"Section: {folder} — {name}\n\n{body}"
                )
                conn.execute(
                    "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                    (SOURCE_LABEL, category, content),
                )
                total += 1

    walk(items, [])
    conn.commit()
    print(f"[termii] postman: {total} request chunks")
    return total


def slugify_category(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "api"


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


def scrape(db_path: Path) -> int:
    conn = init_db(db_path)
    conn.execute("DELETE FROM corpus WHERE source = ?", (SOURCE_LABEL,))
    conn.commit()

    # Section landing pages (server-rendered intros)
    section_total = 0
    for i, slug in enumerate(SECTION_SLUGS, 1):
        url = urljoin(BASE + "/", slug)
        try:
            html = fetch(url)
        except FetchError as e:
            print(f"  [{i}/{len(SECTION_SLUGS)}] FAIL {slug}: {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_S)
            continue
        chunks = extract_section_chunks(url, html, slug)
        if chunks:
            conn.executemany(
                "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                [(SOURCE_LABEL, slugify_category(slug), c) for c in chunks],
            )
            conn.commit()
        section_total += len(chunks)
        print(f"  [{i}/{len(SECTION_SLUGS)}] {slug}: {len(chunks)} chunks")
        time.sleep(REQUEST_DELAY_S)

    # Index/home page intro
    try:
        index_html = fetch(INDEX_URL)
        intro_chunks = extract_section_chunks(INDEX_URL, index_html, "introduction")
        if intro_chunks:
            conn.executemany(
                "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                [(SOURCE_LABEL, "introduction", c) for c in intro_chunks],
            )
            conn.commit()
            section_total += len(intro_chunks)
            print(f"  [intro] {len(intro_chunks)} chunks")
    except FetchError as e:
        print(f"  [intro] FAIL: {e}", file=sys.stderr)

    # Postman collection
    print(f"[termii] fetching postman collection: {POSTMAN_URL}")
    postman_total = scrape_postman(conn)

    total = section_total + postman_total
    conn.close()
    print(f"[termii] done: {total} chunks ({section_total} sections + {postman_total} postman) -> {db_path}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Termii developer docs + Postman collection")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    scrape(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
