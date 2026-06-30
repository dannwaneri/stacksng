"""Monnify (Moniepoint) developer docs scraper for the NaijaCode corpus.

Monnify is Moniepoint/TeamApt's payment gateway. Docs at developers.monnify.com.
Platform: custom Next.js with server-rendered content (no #ssr-props blob,
no exposed OpenAPI spec). We scrape HTML, splitting on h1/h2 inside the
.content-page wrapper.

Content prioritisation note:
  The corpus already has generic checkout / card / transfer coverage from
  Paystack and Flutterwave. Monnify's unique African-stack value lives in:
    - offline-payins / offline-payout  (agent-banking-style cash collection)
    - bills-payment                    (Nigerian utility bill aggregation)
    - reserved-accounts                (NUBAN virtual accounts)
    - verification-api                 (BVN / NIN)
    - direct-debit                     (Nigerian DD with mandates)
  We scrape everything, but each chunk's Source line includes the full path
  so retrieval can match on these distinctive terms.

Source label: 'monnify' (the docs/product brand). category = first path
segment under /docs/ (e.g., 'collections', 'verification-api', 'wallets').
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

BASE = "https://developers.monnify.com"
INDEX_URL = "https://developers.monnify.com/"
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
SOURCE_LABEL = "monnify"

CONTENT_CLASS = "content-page"
TEXT_TAGS = ("p", "pre", "code", "li", "h3", "h4", "h5", "table", "blockquote")
SKIP_PARENT_TAGS = {"nav", "footer", "aside"}


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _fetch_once(url: str) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise FetchError("curl not found on PATH")
    cmd = [
        curl, "-sS", "-L", "--compressed",
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


RETRY_STATUSES = {429, 500, 502, 503, 504}


def fetch(url: str) -> str:
    for attempt in range(MAX_RETRIES_429 + 1):
        try:
            return _fetch_once(url)
        except FetchError as e:
            if e.status in RETRY_STATUSES and attempt < MAX_RETRIES_429:
                wait = RATE_LIMIT_BACKOFF_S * (attempt + 1)
                print(f"  transient {e.status}, backing off {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
    raise FetchError("retries exhausted")


def discover_doc_urls(index_html: str) -> list[str]:
    soup = BeautifulSoup(index_html, "html.parser")
    seen: set[str] = set()
    for a in soup.select('a[href^="/docs/"]'):
        href = a.get("href", "").split("#", 1)[0].split("?", 1)[0]
        if href in ("/docs", "/docs/"):
            continue
        href = href.rstrip("/")
        seen.add(urljoin(BASE, href))
    return sorted(seen)


def category_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
    # path always starts with 'docs' for these URLs
    if parts and parts[0] == "docs":
        parts = parts[1:]
    return parts[0] if parts else "monnify"


def _under_skipped_parent(el: Tag) -> bool:
    return el.find_parent(list(SKIP_PARENT_TAGS)) is not None


def find_content_root(soup: BeautifulSoup) -> Tag | None:
    """Return the .content-page wrapper or fall back to <body>."""
    el = soup.find(class_=re.compile(rf"\b{re.escape(CONTENT_CLASS)}\b"))
    if isinstance(el, Tag):
        return el
    return soup.find("main") or soup.find("article") or soup.body


def extract_chunks(url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    root = find_content_root(soup)
    if root is None:
        return []

    headings = [h for h in root.find_all(["h1", "h2"]) if not _under_skipped_parent(h)]
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
            if _under_skipped_parent(el):
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
    print(f"[monnify] fetching index: {INDEX_URL}")
    index_html = fetch(INDEX_URL)
    urls = discover_doc_urls(index_html)
    print(f"[monnify] discovered {len(urls)} doc pages")

    conn = init_db(db_path)
    conn.execute("DELETE FROM corpus WHERE source = ?", (SOURCE_LABEL,))
    conn.commit()

    total = 0
    for i, url in enumerate(urls, 1):
        category = category_from_url(url)
        try:
            html = fetch(url)
        except FetchError as e:
            if e.status == 404:
                print(f"  [{i}/{len(urls)}] skip {url}: 404")
            else:
                print(f"  [{i}/{len(urls)}] FAIL {url}: {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_S)
            continue

        chunks = extract_chunks(url, html)
        if chunks:
            conn.executemany(
                "INSERT INTO corpus (source, category, content) VALUES (?, ?, ?)",
                [(SOURCE_LABEL, category, c) for c in chunks],
            )
            conn.commit()
        total += len(chunks)
        page_slug = urlparse(url).path.rsplit("/", 1)[-1] or category
        print(f"  [{i}/{len(urls)}] {category}/{page_slug}: {len(chunks)} chunks")
        time.sleep(REQUEST_DELAY_S)

    conn.close()
    print(f"[monnify] done: {total} chunks -> {db_path}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape Monnify (Moniepoint) developer docs")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    scrape(args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
