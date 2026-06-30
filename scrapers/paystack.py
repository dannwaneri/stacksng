"""Paystack API docs scraper for the NaijaCode corpus.

Crawls https://paystack.com/docs/api/ and each resource page, splits content on
h1/h2 boundaries (one chunk per API operation), and writes rows to corpus.db.

Schema:
  corpus(id, source='paystack', category=<resource-slug>, content=<chunk>)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

BASE = "https://paystack.com"
INDEX_URL = "https://paystack.com/docs/api/"
PAYMENTS_SEED = "https://paystack.com/docs/payments/webhooks/"
SECTION_PREFIXES = ("/docs/api/", "/docs/payments/")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_DELAY_S = 0.5
TIMEOUT_S = 30

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "corpus.db"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

SKIP_PARENT_TAGS = {"nav", "footer", "aside"}
SKIP_PARENT_CLASS_SUBSTRINGS = ("layout__header", "layout__nav", "layout__footer")
TEXT_TAGS = ("p", "pre", "li", "h3", "h4", "h5", "table")


class FetchError(RuntimeError):
    pass


def fetch(url: str) -> str:
    """Fetch via curl subprocess to bypass TLS fingerprinting that blocks requests."""
    curl = shutil.which("curl")
    if not curl:
        raise FetchError("curl not found on PATH")
    cmd = [
        curl,
        "-sS",
        "--fail",
        "--compressed",
        "--max-time", str(TIMEOUT_S),
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: en-US,en;q=0.9",
        "-H", "Referer: https://paystack.com/docs/",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FetchError(f"curl exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    return proc.stdout


def discover_resource_urls(index_html: str, prefix: str = "/docs/api/") -> list[str]:
    soup = BeautifulSoup(index_html, "html.parser")
    seen: set[str] = set()
    selector = f'a[href^="{prefix}"]'
    for a in soup.select(selector):
        href = a.get("href", "").split("#", 1)[0].split("?", 1)[0]
        if href in ("", prefix.rstrip("/"), prefix):
            continue
        if not href.endswith("/"):
            href += "/"
        seen.add(urljoin(BASE, href))
    return sorted(seen)


def category_from_url(url: str) -> str:
    """Slug after the section prefix (e.g., '/docs/payments/webhooks/' -> 'webhooks')."""
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "index"


def category_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1]
    return slug or "index"


def _under_skipped_parent(el: Tag) -> bool:
    if el.find_parent(list(SKIP_PARENT_TAGS)) is not None:
        return True
    for ancestor in el.parents:
        classes = ancestor.get("class") if isinstance(ancestor, Tag) else None
        if not classes:
            continue
        joined = " ".join(classes)
        if any(s in joined for s in SKIP_PARENT_CLASS_SUBSTRINGS):
            return True
    return False


def extract_chunks(url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        return []

    headings = [h for h in main.find_all(["h1", "h2"]) if not _under_skipped_parent(h)]
    if not headings:
        return []

    chunks: list[str] = []
    for i, heading in enumerate(headings):
        next_heading = headings[i + 1] if i + 1 < len(headings) else None
        anchor = heading.get("id", "")
        title = heading.get_text(" ", strip=True).replace("¶", "").strip()

        parts: list[str] = []
        for el in heading.find_all_next():
            if el is next_heading:
                break
            if not isinstance(el, Tag):
                continue
            if el.name not in TEXT_TAGS:
                continue
            if _under_skipped_parent(el):
                continue
            text = el.get_text(" ", strip=True)
            if not text:
                continue
            parts.append(text)

        body = "\n".join(parts)
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        if not body:
            continue

        cite = f"{url}#{anchor}" if anchor else url
        chunks.append(f"Source: {cite}\nSection: {title}\n\n{body}")
    return chunks


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


def _urls_already_scraped(conn: sqlite3.Connection, source_label: str, prefix: str) -> set[str]:
    """Return the set of URLs from this source that already have rows in corpus.

    We detect this by looking for chunks whose 'Source: <url>...' first line
    matches the given prefix.
    """
    rows = conn.execute(
        "SELECT DISTINCT SUBSTR(content, 9, INSTR(content || '#', '#') - 9) "
        "FROM corpus WHERE source = ? AND content LIKE ?",
        (source_label, f"Source: %{prefix}%"),
    ).fetchall()
    out: set[str] = set()
    for (url,) in rows:
        if url and url.startswith("http"):
            # strip any trailing whitespace and normalise to /trailing-slash form
            u = url.split("\n", 1)[0].strip()
            if not u.endswith("/"):
                u += "/"
            out.add(u)
    return out


def scrape(db_path: Path, source_label: str = "paystack", refresh: bool = False) -> int:
    """Scrape Paystack docs.

    Two sections: /docs/api/ (API reference) and /docs/payments/ (integration guides).
    By default this is *additive* — it only inserts URLs not already in the corpus.
    Pass refresh=True (or --refresh on the CLI) to delete-and-replace.
    """
    print(f"[paystack] fetching api index: {INDEX_URL}")
    api_index_html = fetch(INDEX_URL)
    api_urls = discover_resource_urls(api_index_html, "/docs/api/")
    api_urls = [INDEX_URL] + [u for u in api_urls if u != INDEX_URL]

    print(f"[paystack] fetching payments index: {PAYMENTS_SEED}")
    payments_seed_html = fetch(PAYMENTS_SEED)
    payments_urls = discover_resource_urls(payments_seed_html, "/docs/payments/")
    if PAYMENTS_SEED not in payments_urls:
        payments_urls = [PAYMENTS_SEED] + payments_urls

    sections = [
        ("api", api_urls, api_index_html, INDEX_URL),
        ("payments", payments_urls, payments_seed_html, PAYMENTS_SEED),
    ]

    conn = init_db(db_path)
    if refresh:
        print("[paystack] --refresh: deleting all existing paystack rows")
        conn.execute("DELETE FROM corpus WHERE source = ?", (source_label,))
        conn.commit()
        already = set()
    else:
        already = _urls_already_scraped(conn, source_label, "/docs/")
        if already:
            print(f"[paystack] additive mode: {len(already)} URLs already in corpus, will skip")

    total = 0
    for section_name, urls, seed_html, seed_url in sections:
        print(f"[paystack] section: {section_name} ({len(urls)} pages)")
        for i, url in enumerate(urls, 1):
            if url in already:
                print(f"  [{section_name} {i}/{len(urls)}] skip (already in corpus): {url}")
                continue
            category = category_from_url(url)
            try:
                html = seed_html if url == seed_url else fetch(url)
            except FetchError as e:
                print(f"  [{section_name} {i}/{len(urls)}] FAIL {url}: {e}", file=sys.stderr)
                continue

            chunks = extract_chunks(url, html)
            conn.executemany(
                "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                [(source_label, category, c) for c in chunks],
            )
            conn.commit()
            print(f"  [{section_name} {i}/{len(urls)}] {category}: {len(chunks)} chunks")
            total += len(chunks)

            if url != urls[-1]:
                time.sleep(REQUEST_DELAY_S)

    conn.close()
    print(f"[paystack] done: {total} new chunks written to {db_path}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Paystack docs (api + payments guides)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--refresh", action="store_true", help="Delete existing paystack rows and rescrape from scratch")
    args = parser.parse_args()
    scrape(args.db, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
