"""Query the StacksNG corpus end-to-end.

Pipeline:
  1. Embed the user's question via Ollama (nomic-embed-text).
  2. Cosine-similarity rank against every stored embedding.
  3. Pull top-K chunks, assemble a prompt with citations.
  4. Stream the answer from Ollama (qwen2.5-coder:7b) and print citations.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "corpus.db"

OLLAMA_BASE = "http://localhost:11434"
EMBED_MODEL = "nomic-embed-text"
DEFAULT_CHAT_MODEL = "qwen2.5-coder:7b"
DEFAULT_TOP_K = 5
DEFAULT_CONTEXT_CHAR_BUDGET = 8000
DEFAULT_MAX_TOKENS = 512


SYSTEM_PROMPT = """You are NaijaCode, an offline coding assistant specialised in the African developer stack: Paystack, Flutterwave, Moniepoint/Monnify, Termii, NGN/kobo currency handling, BVN/NIN verification, USSD, and Nigerian banking integrations.

You will be given relevant documentation excerpts as context. Use them — and only them — to answer the user's question. Each excerpt is preceded by a Source: line.

Rules:
- Before answering, check whether the specific company/product/provider named in the question (e.g. a payment provider's brand name) is actually named in the context excerpts below. Retrieval is similarity-based and will sometimes hand you excerpts from a DIFFERENT provider just because the topic (webhooks, checkout, transfers) is similar — that is not the same as the named provider being covered.
- If the named provider in the question does not appear in the context excerpts, say plainly that this provider is not in your knowledge base. Do NOT substitute another provider's instructions, endpoints, or headers under the asked-about provider's name, and do NOT invent a plausible-looking source URL. Only mention the other provider's actual documented flow if you clearly label it as being for that other provider, not the one asked about.
- If the context does not contain enough information to answer confidently, say so plainly.
- Quote endpoint paths, parameter names, and code from the context verbatim.
- When you cite a specific fact, append the source URL in parentheses, e.g. (source: https://paystack.com/docs/api/transaction/#verify). Never cite a URL that does not appear in the context excerpts.
- Prefer short, working code over prose. Use the language the user asks for, or fall back to the language already used in the context excerpt.
"""


# Retrieval similarity can't reliably tell "same topic, different provider"
# apart from "provider actually covered" when the topic is generic — webhook
# signature verification is near-identical HMAC-SHA512 across providers, so a
# PalmPay question retrieves Paystack/Monnify chunks at high similarity. The
# SYSTEM_PROMPT instruction asking the model to self-check this measured only
# 33% (Kuda) / 0% (PalmPay) reliable decline rate across repeated runs at
# temperature=0.2 with no fixed seed (retest, 2026-08-16). For any brand name
# enumerable ahead of time, skip the LLM and decline deterministically instead
# of relying on a probabilistic instruction to hold.

CORPUS_PROVIDER_ALIASES: dict[str, str] = {
    "paystack": "Paystack",
    "flutterwave": "Flutterwave",
    "monnify": "Monnify",
    "moniepoint": "Monnify",
    "termii": "Termii",
}

# Deliberately excludes ordinary commercial/settlement banks (GTBank, Access
# Bank, Wema, Sterling, VFD, Providus, 9PSB). A DEV.to reader (Heinrich Neb)
# found these caused false declines: "How do I implement BVN verification for
# a customer with a Wema Bank account?" was declining even though BVN
# verification is genuinely covered — Wema was just the customer's bank, not
# the API being asked about. Those banks are near-universal incidental
# context in the corpus's own strongest material (NUBAN transfers, dedicated
# virtual accounts, BVN-linked accounts), so gating on their names silently
# breaks legitimate in-scope questions. Only gate on entities where "asking
# about their API" is the dominant reading: competing payment/fintech
# providers, not the banks that sit underneath a covered provider's rails.
KNOWN_OUT_OF_CORPUS_PROVIDERS: dict[str, str] = {
    "kuda bank": "Kuda Bank",
    "kuda": "Kuda Bank",
    "palmpay": "PalmPay",
    "interswitch": "Interswitch",
    "quickteller": "Interswitch (Quickteller)",
    "paga": "Paga",
    "opay": "OPay",
    "carbon": "Carbon",
    "fairmoney": "FairMoney",
    "cowrywise": "Cowrywise",
    "piggyvest": "PiggyVest",
    "chipper cash": "Chipper Cash",
    "remita": "Remita",
    "m-pesa": "M-Pesa",
    "mpesa": "M-Pesa",
    "mtn momo": "MTN MoMo",
    "ozow": "Ozow",
    "stripe": "Stripe",
    "razorpay": "Razorpay",
}


# Some brand names are also ordinary English words, so a bare word-boundary
# match false-fires on their common non-brand use. Found by a DEV.to reader
# (Heinrich Neb, 2026-08-21): "How do I add a carbon copy recipient to my
# transactional emails?" declined, citing Carbon the lender, on a question
# with no fintech brand in it at all. Same risk with "stripe" ("a racing
# stripe design", "a magnetic stripe card"). These two collocations are
# stripped from the text before matching, so a genuine standalone mention
# ("Carbon's loan API") still gates correctly.
AMBIGUOUS_PROVIDER_EXCLUSIONS: dict[str, re.Pattern] = {
    "carbon": re.compile(
        r"\bcarbon\s+(copy|copies|footprint|dioxide|neutral|fiber|fibre|emissions?)\b"
    ),
    "stripe": re.compile(
        r"\b(racing|pin|magnetic|card|zebra)\s+stripes?\b"
        r"|\bstripes?\s+(pattern|design|shirt|card)\b"
    ),
}


def _find_provider_mentions(question: str, aliases: dict[str, str]) -> set[str]:
    """Word-boundary, case-insensitive match of known brand aliases in the question."""
    q = question.lower()
    found: set[str] = set()
    for alias, canonical in sorted(aliases.items(), key=lambda kv: -len(kv[0])):
        exclusion = AMBIGUOUS_PROVIDER_EXCLUSIONS.get(alias)
        text = exclusion.sub(" ", q) if exclusion else q
        if re.search(rf"\b{re.escape(alias)}\b", text):
            found.add(canonical)
    return found


def check_provider_gate(question: str) -> str | None:
    """Deterministic pre-generation gate for known out-of-corpus providers.

    Returns a decline message if the question names a provider we know is
    NOT in the corpus and does not also name one that is (a comparison
    question naming both is left to retrieval + the LLM). Returns None to
    proceed normally.
    """
    out_of_corpus = _find_provider_mentions(question, KNOWN_OUT_OF_CORPUS_PROVIDERS)
    in_corpus = _find_provider_mentions(question, CORPUS_PROVIDER_ALIASES)
    if out_of_corpus and not in_corpus:
        names = " / ".join(sorted(out_of_corpus))
        return (
            f"{names} is not in my knowledge base. This assistant's corpus covers "
            f"Paystack, Flutterwave, Monnify, and Termii only. I won't provide "
            f"instructions for {names} under another provider's documented flow, "
            f"since their APIs, headers, and endpoints differ and a substituted "
            f"answer would be wrong. Please refer to {names}'s official developer "
            f"documentation."
        )
    return None


def embed_query(text: str) -> np.ndarray:
    payload = json.dumps({"model": EMBED_MODEL, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return np.asarray(data["embedding"], dtype=np.float32)


def load_index(db_path: Path, model: str = EMBED_MODEL) -> tuple[np.ndarray, list[tuple[int, str, str, str]]]:
    """Returns (matrix [N x dim], rows [(corpus_id, source, category, content), ...])."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        """
        SELECT c.id, c.source, c.category, c.content, e.vector, e.dim
        FROM corpus c
        JOIN embeddings e ON e.corpus_id = c.id
        WHERE e.model = ?
        ORDER BY c.id
        """,
        (model,),
    )
    rows: list[tuple[int, str, str, str]] = []
    vectors: list[np.ndarray] = []
    for cid, src, cat, content, vec_bytes, dim in cur:
        vec = np.frombuffer(vec_bytes, dtype=np.float32)
        if vec.shape[0] != dim:
            continue
        vectors.append(vec)
        rows.append((cid, src, cat, content))
    conn.close()
    if not vectors:
        return np.zeros((0, 0), dtype=np.float32), []
    return np.vstack(vectors), rows


def search(
    query_vec: np.ndarray,
    matrix: np.ndarray,
    rows: list[tuple[int, str, str, str]],
    top_k: int,
    source_filter: str | None = None,
) -> list[tuple[float, int, str, str, str]]:
    if matrix.size == 0:
        return []
    q = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    m_norms = np.linalg.norm(matrix, axis=1) + 1e-9
    sims = (matrix @ q) / m_norms
    order = np.argsort(-sims)
    out: list[tuple[float, int, str, str, str]] = []
    for idx in order:
        cid, src, cat, content = rows[idx]
        if source_filter and src != source_filter:
            continue
        out.append((float(sims[idx]), cid, src, cat, content))
        if len(out) >= top_k:
            break
    return out


def build_prompt(question: str, hits: list[tuple[float, int, str, str, str]], char_budget: int) -> str:
    pieces: list[str] = []
    used = 0
    for rank, (score, cid, src, cat, content) in enumerate(hits, 1):
        header = f"[CHUNK {rank} • {src}/{cat} • similarity={score:.3f}]\n"
        chunk = header + content
        if used + len(chunk) > char_budget and pieces:
            break
        pieces.append(chunk)
        used += len(chunk) + 2

    context_block = "\n\n---\n\n".join(pieces) if pieces else "(no relevant context found)"
    return (
        f"Question: {question}\n\n"
        f"Context excerpts (most relevant first):\n\n{context_block}\n\n"
        f"Answer the question using the excerpts above. Cite source URLs for any specific claim."
    )


def stream_chat(model: str, system: str, user: str, max_tokens: int) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    collected: list[str] = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk = (obj.get("message") or {}).get("content") or ""
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                collected.append(chunk)
            if obj.get("done"):
                break
    sys.stdout.write("\n")
    return "".join(collected)


def main() -> int:
    # Windows consoles default to a legacy code page (e.g. cp1252) that can't
    # encode characters like em-dashes the model generates — crashed mid-answer
    # on C13 of the corpus stress test (2026-08-03) with UnicodeEncodeError.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Query the StacksNG corpus with RAG")
    parser.add_argument("question", nargs="*", help="The question to ask (or use --question)")
    parser.add_argument("--question", "-q", dest="question_opt", help="Alternative way to pass the question")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--budget", type=int, default=DEFAULT_CONTEXT_CHAR_BUDGET)
    parser.add_argument("--source", help="Restrict retrieval to a single source (paystack, flutterwave, monnify, termii)")
    parser.add_argument("--model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help="Cap generated tokens per answer (Ollama num_predict)")
    parser.add_argument("--retrieval-only", action="store_true", help="Skip the LLM call; just print the top hits")
    args = parser.parse_args()

    question = " ".join(args.question).strip() or (args.question_opt or "").strip()
    if not question:
        parser.error("provide a question as positional arg or --question")

    print(f"Q: {question}\n")

    gate_message = check_provider_gate(question)
    if gate_message is not None:
        print("=== Answer (deterministic provider gate — no LLM call) ===")
        print(gate_message)
        print("\n=== Sources ===")
        print("  (none — question declined before retrieval)")
        return 0

    matrix, rows = load_index(args.db)
    if not rows:
        print("[query] no embeddings found. Run scripts/embed.py first.", file=sys.stderr)
        return 1
    print(f"[query] index loaded: {len(rows)} embedded chunks, dim={matrix.shape[1]}")

    qv = embed_query(question)
    hits = search(qv, matrix, rows, top_k=args.top_k, source_filter=args.source)

    print(f"\n=== Top {len(hits)} retrieved chunks ===")
    for rank, (score, cid, src, cat, content) in enumerate(hits, 1):
        first_two = "\n".join(content.splitlines()[:2])
        print(f"  {rank}. sim={score:.3f}  {src}/{cat}  (id={cid})")
        print(f"     {first_two[:140]}")
    print()

    if args.retrieval_only:
        return 0

    prompt = build_prompt(question, hits, args.budget)
    print(f"=== Answer ({args.model}, max {args.max_tokens} tokens) ===")
    stream_chat(args.model, SYSTEM_PROMPT, prompt, args.max_tokens)

    print("\n=== Sources ===")
    seen: set[str] = set()
    for _score, _cid, src, cat, content in hits:
        first_line = content.splitlines()[0] if content else ""
        if first_line.startswith("Source: "):
            url = first_line[len("Source: "):]
            if url not in seen:
                seen.add(url)
                print(f"  - {url}  ({src}/{cat})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
