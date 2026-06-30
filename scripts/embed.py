"""Embed corpus rows into vectors using Ollama's nomic-embed-text.

Reads every row from corpus.corpus that doesn't yet have an embedding row,
calls Ollama /api/embeddings, stores float32 vector bytes in
corpus.embeddings.

Re-run is idempotent: rows already embedded with the same model are skipped.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "corpus.db"
SCHEMA_PATH = PROJECT_ROOT / "db" / "schema.sql"

OLLAMA_URL = "http://localhost:11434/api/embeddings"
DEFAULT_MODEL = "nomic-embed-text"
REQUEST_TIMEOUT_S = 60

# nomic-embed-text claims 8192-token context, but in practice we see HTTP 500s
# at ~10KB chars and above. Truncating the EMBED INPUT (not the stored content)
# at 6000 chars keeps us safely inside the window while still capturing the
# distinctive head of every chunk for retrieval purposes. The LLM call later
# uses the full stored content.
EMBED_INPUT_MAX_CHARS = 6000
RETRY_5XX_BACKOFF_S = 4


def _post_embed(text: str, model: str) -> list[float]:
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    emb = data.get("embedding")
    if not isinstance(emb, list) or not emb:
        raise RuntimeError(f"unexpected Ollama response: {list(data.keys())}")
    return emb


def call_ollama_embed(text: str, model: str) -> list[float]:
    """Embed `text` via Ollama with truncation + one retry on 5xx."""
    truncated = text[:EMBED_INPUT_MAX_CHARS]
    try:
        return _post_embed(truncated, model)
    except urllib.error.HTTPError as e:
        if 500 <= e.code < 600:
            time.sleep(RETRY_5XX_BACKOFF_S)
            # Retry with a more aggressive truncation in case the model is the bottleneck.
            return _post_embed(truncated[: EMBED_INPUT_MAX_CHARS // 2], model)
        raise


def ensure_schema(conn: sqlite3.Connection) -> None:
    if SCHEMA_PATH.exists():
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed corpus rows via Ollama")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=0, help="Embed at most N rows (0 = all)")
    parser.add_argument("--reembed", action="store_true", help="Re-embed even rows that already have a vector for this model")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    if args.reembed:
        rows = conn.execute(
            "SELECT id, content FROM corpus ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT c.id, c.content
            FROM corpus c
            LEFT JOIN embeddings e ON e.corpus_id = c.id AND e.model = ?
            WHERE e.corpus_id IS NULL
            ORDER BY c.id
            """,
            (args.model,),
        ).fetchall()

    if args.limit > 0:
        rows = rows[: args.limit]

    print(f"[embed] {len(rows)} rows to embed with model={args.model}")
    if not rows:
        print("[embed] nothing to do")
        return 0

    started = time.time()
    for i, (cid, content) in enumerate(rows, 1):
        try:
            vec = call_ollama_embed(content, args.model)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as e:
            print(f"  [{i}/{len(rows)}] FAIL id={cid}: {e}", file=sys.stderr)
            continue
        arr = np.asarray(vec, dtype=np.float32)
        conn.execute(
            """
            INSERT INTO embeddings (corpus_id, model, dim, vector)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(corpus_id) DO UPDATE SET
              model = excluded.model,
              dim = excluded.dim,
              vector = excluded.vector
            """,
            (cid, args.model, arr.shape[0], arr.tobytes()),
        )
        if i % 25 == 0 or i == len(rows):
            conn.commit()
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            print(f"  [{i}/{len(rows)}] committed, {rate:.1f} rows/s")

    conn.commit()
    print(f"[embed] done in {time.time() - started:.1f}s")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
