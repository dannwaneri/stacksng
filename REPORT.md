# Technical Report — StacksNG

**Team ID:** 1067588-stacksng
**Domain:** coding_assistants
**Model:** qwen2.5-coder-7b-Q4_K_M

StacksNG is an offline coding assistant for the African fintech stack: a
780-chunk RAG corpus built from Paystack, Flutterwave, Monnify (Moniepoint),
and Termii documentation, retrieved by local embeddings and answered by
qwen2.5-coder:7b — no internet, no API keys, every answer cited back to the
official docs.

---

## Problem

Nigerian and African developers build on a different stack than the rest of
the world. Paystack, Flutterwave, Moniepoint, USSD flows, NGN/kobo currency
handling, and BVN verification are the primitives of African fintech
development — and they are precisely the topics where general-purpose coding
assistants are weakest, because these APIs are thinly represented in training
data and change faster than model retraining cycles.

The failure mode is not "no answer" — it is a confidently wrong answer: a
hallucinated endpoint, an amount passed in naira when the API expects kobo, a
webhook accepted without signature verification. In payments code, each of
those is a production incident or a security hole.

StacksNG's answer is grounding: every response is generated from retrieved
passages of the official documentation and cites the exact source URL, so the
developer can verify the claim in one click — or trust it offline when there
is no connectivity to verify with.

Target user: African software developers building fintech integrations on
laptops they already own, under data and power constraints they don't control.

Distribution is not hypothetical. Nigeria and the wider region already have
active developer communities built around exactly this stack — Data Science
Nigeria, Zindi, Deep Learning Indaba, and Microsoft's Africa Development
Centre — where fintech-integration questions come up routinely. StacksNG
ships as an open-source CLI so it can be picked up there directly, not sold
into a fintech's compliance department: the customer is the developer
already building against these APIs, not the bank.

---

## What the test prompts will exercise

Judges' accuracy runs hit the full pipeline: query embedding → cosine
retrieval over 780 chunks → cited generation. Retrieval for both registered
test prompts lands on the correct documentation section (measured on this
machine, `--retrieval-only`):

| Test prompt | Top-1 retrieved chunk | Cosine sim |
|---|---|---|
| tp_001 — Paystack webhook signature in Node.js | `paystack.com/docs/payments/webhooks/#verify-event-origin` | 0.743 |
| tp_002 — Flutterwave payment init in Python | `developer.flutterwave.com` collections-inflow guide | 0.713 |

For tp_001 the grounded answer contains the three facts that matter — HMAC
**SHA-512** over the raw request body, keyed with the **secret key**, compared
against the **`x-paystack-signature`** header — with the source URL appended.
An ungrounded model frequently gets the hash algorithm or header name wrong;
this is exactly the class of error the corpus eliminates.

---

## Architecture

```
question ──► nomic-embed-text (Ollama, local)
                 │ 768-d query vector
                 ▼
         cosine top-K over 780 embedded chunks (SQLite + numpy)
                 │ top-5 chunks, each carrying "Source: <url>" header
                 ▼
         prompt assembly (context + citation instructions)
                 │
                 ▼
         qwen2.5-coder:7b Q4_K_M (llama.cpp, CPU) ──► streamed answer + source URLs
```

Corpus composition:

| Source | Chunks | Acquisition |
|---|---|---|
| Paystack | 340 | HTML scrape of `/docs/api/` + `/docs/payments/` guides |
| Monnify (Moniepoint) | 290 | HTML scrape of custom Next.js docs portal |
| Flutterwave | 114 | Embedded OpenAPI spec + guide markdown (ReadMe `#ssr-props`) |
| Termii | 36 | HTML + published Postman collection |
| **Total** | **780** | 100 % embedded, one source URL per chunk |

---

## The African use case — and why it is load-bearing

The cross-disciplinary pairing (fintech) is not thematic garnish; the corpus
content is the product. Verifiable against the shipped `corpus.db`:

| Uniquely African content | Chunks |
|---|---|
| NGN / kobo / currency-subunit handling | 178 |
| Bank transfer flows (NUBAN rails) | 34 |
| Direct debit + mandates (Nigerian DD scheme) | 30 + 35 |
| USSD payment flows | 26 |
| BVN / NIN identity verification | 27 + 15 |
| Mobile money (MTN, Airtel rails) | 25 |
| Reserved virtual accounts (NUBAN) | 17 |
| Offline pay-ins via agency banking ("Moniepoint Business Owner" locations) | 7 |

The last row is content that exists in no Western coding assistant's mental
model: cash collection through Moniepoint's agent network — agents in every
local government area in Nigeria — surfaced to merchants as an API. A
developer asking about it gets a grounded answer here and a hallucination
anywhere else.

Offline-first is equally load-bearing. Cloud assistants assume three things
an African developer cannot: uncapped data (mobile data is metered and priced
against far lower median incomes), stable connectivity, and continuous grid
power. StacksNG runs entirely from local SQLite + a local GGUF; once
installed it works on generator power in a co-working space in Port Harcourt
exactly as it does on fibre in London.

Every claim above is checkable by judges: open `corpus.db`, or run
`python scripts/query.py --retrieval-only "<any question>"` to see retrieval
without invoking the LLM.

---

## Design Decisions

- **Base model: qwen2.5-coder:7b** — purpose-built for code generation, and
  the largest coder model that fits the 8 GB envelope at Q4_K_M. We measured
  the smaller alternative rather than assuming: a 1.5B ablation (see
  Benchmarks) wins the score formula by 35 points but fabricates a
  nonexistent client library on one of our two registered test prompts. In
  payments code, "close" is a bug.
- **Quantization: Q4_K_M** — Q8_0 exceeded the memory budget; Q2_K degraded
  code generation unacceptably (mangled identifiers, broken JSON).
- **RAG over fine-tuning** — the corpus is documentation, not instruction
  pairs. RAG gives citation traceability (every answer carries source URLs)
  and the corpus can be re-scraped in minutes when an API changes, with no
  retraining. The scrapers ship in `scrapers/`.
- **SQLite + numpy over a vector DB** — 780 × 768-d float32 vectors is
  ~2.3 MB; brute-force cosine over it takes milliseconds. A vector database
  would add an install dependency for zero benefit at this scale, and every
  dependency matters when the target machine is offline.
- **Runtime:** llama.cpp via Ollama for development; raw GGUF + llama-bench
  for the profiler run.

---

## Constraints

- Target: 8 GB RAM, integrated graphics, no discrete GPU.
- Pure CPU inference via llama.cpp.
- No internet dependency at runtime — corpus is local SQLite, model is local GGUF.
- Power unreliability — the assistant must be useful in short sessions
  between outages, which favours grounded, concise, correct-first-time answers
  over long exploratory generations.

---

## Benchmarks

Measured by `adtc-profiler run --mode participant --skip-accuracy` on the
development machine. See `submission.json` for the full report.

| Metric | Value |
|---|---|
| Machine | Dell i5 11th Gen, 16 GB RAM (15.7 GB usable), Intel Iris Xe |
| OS | Windows 11 10.0.26100 |
| Runtime | llama.cpp (b9847) via `llama-bench`, pure CPU |
| RAM at peak (RSS) | 7053.67 MB (6.888 GB) |
| Steady-state RSS | 6505.94 MB |
| Time to first token | 33,464.86 ms (cold mmap of 4.68 GB model + 512-token prompt eval) |
| Generation speed | 4.82 t/s |
| Prompt tokens / Generated tokens | 512 / 128 |
| CPU p99 utilisation | 99.6 % |
| Thermal throttling | None observed |
| Context length | 32,768 |

### Self-reported scores (Sacc placeholder = 0)

| Component | Formula | Value |
|---|---|---|
| Sperf | `min(TPS / 15.0, 1.0) * 100` | **32.13** |
| Seff | `max(0, (7.0 - peak_rss_gb) / 7.0) * 100` | **1.60** |
| Pthermal | `10 if throttled else 0` | **0** |
| Sacc | judged on validation set | 0 (placeholder) |
| **Stotal** | `0.50*Sacc + 0.30*Sperf + 0.20*Seff − Pthermal` | **9.96** |

### Why these numbers, and what we trade

A 7B Q4_K_M model is the largest that fits the 8 GB budget. Peak RSS at
6.89 GB sits ~110 MB under the 7 GB Seff ceiling, so Seff is structurally
near zero **by choice**: Sacc carries 50 % of the total score and Seff 20 %,
and in this domain accuracy is the difference between working payments code
and a security incident. We spend the memory budget where the scoring — and
the user — put the weight.

The 33.5 s first-token figure is the profiler's fully cold measurement: it
includes mapping the 4.68 GB model file from disk and evaluating a 512-token
prompt. In interactive use the model file stays in the OS page cache after
the first query, which removes the load component; time to first token then
scales with prompt length. Generation speed is unchanged either way (4.82 t/s).

### The 1.5B ablation — measuring the trade instead of asserting it

The strongest formula play in this competition is a small model. We tested
it: qwen2.5-coder-**1.5b** at the same Q4_K_M quantization, through the
identical RAG pipeline, profiled by the same `adtc-profiler` on the same
machine.

| | 7B (submitted) | 1.5B (ablation) |
|---|---|---|
| Generation speed | 4.82 t/s | 16.90 t/s |
| First token (cold) | 33.5 s | 6.6 s |
| Peak RSS | 6.888 GB | 1.672 GB |
| Sperf / Seff | 32.13 / 1.60 | 100.00 / 76.11 |
| Non-accuracy score (`0.3·Sperf + 0.2·Seff`) | **9.96** | **45.22** |

On everything the formula measures, the 1.5B wins by ~35 points. We rejected
it because of what happened on the registered test prompts:

- **tp_001 (Paystack webhook):** tie. Both models produce the correct
  HMAC-SHA512 / secret key / `x-paystack-signature` answer, because the
  retrieved chunk contains Paystack's reference implementation and
  transcription is sufficient. Retrieval equalizes lookup questions.
- **tp_002 (Flutterwave payment init):** the 1.5B opened with
  `import flutterwave` and a `flutterwave.Client(...)` API — **a Python
  client library that does not exist** — wrapping otherwise
  correctly-grounded payload fields. The 7B, given identical context, wrote
  `requests` calls against the real documented endpoints
  (`/customers`, `/payment-methods`) with the real headers (`X-Trace-Id`,
  `X-Idempotency-Key`). Model capacity still decides synthesis questions.

A payments assistant that fabricates the client library on half of its
registered prompts is a fast, memory-efficient way to ship broken code. We
submit the 7B: the memory budget is spent where both the scoring weights
(Sacc = 50 %) and the user's safety put it.

These are self-reported development benchmarks. Official scores are measured
by the ADTC profiler on the standard evaluation machine.

---

## Reproducibility

```bash
git clone https://github.com/dannwaneri/stacksng
cd stacksng
bash download_model.sh        # fetches qwen2.5-coder-7b-Q4_K_M.gguf (~4.7 GB, public URL)
pip install -r requirements.txt
python scripts/query.py --retrieval-only "How do I verify a Paystack webhook signature?"
```

`download_model.sh` pulls a GGUF that is byte-identical (4,683,074,048 bytes)
to the weights profiled in `submission.json`. The corpus (`corpus.db`), the
scrapers that built it (`scrapers/`), and the embedding/query pipeline
(`scripts/`) all ship in the repo.

---

## Roadmap

Africa's Talking USSD documentation for deeper session-flow coverage; then
Ghana (MTN MoMo), Kenya (M-Pesa/Daraja), and South Africa (Ozow) corpora —
the architecture adds a market by adding a scraper.
