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

Only two boxes in that diagram touch a model — the embedding call and the final
generation call. Everything else (scraping, chunking, cosine ranking, prompt
assembly, citation extraction) is deterministic Python: same query, same corpus,
same retrieved chunks and prompt, every time. What the model sees is fully
reasoned-about code; only the final answer generation is genuinely
non-deterministic.

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
  the largest coder model that fits the 8 GB envelope at Q4_K_M. I measured
  the smaller alternative rather than assuming: a 1.5B ablation (see
  Benchmarks) wins the score formula by 35 points but fabricates a
  nonexistent client library on one of my two registered test prompts. In
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
  Verified, not assumed: ran `scripts/query.py` end-to-end with a Windows Firewall
  outbound-block rule on `python.exe` (loopback exempted, so the process could
  still reach the local Ollama daemon but nothing else) — retrieval, generation,
  and citations all completed successfully with zero internet access available.
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

The table above is the original dev-box measurement (16GB Windows box, no
enforced ceiling) — kept for context. It was superseded on Aug 3-4 2026 by
runs inside a Docker container matching the actual reference profile exactly
(`--memory=7.5g --cpus=4`, `Dockerfile.profiler`, pinned `llama.cpp` b10240,
CPU-only, Ubuntu 22.04), verified with `adtc-profiler compare` (`verdict: PASS`,
all checks within official tolerance — see `artifacts/verdict.json`). These
are the figures actually entered on Devpost:

| Component | Formula | Value |
|---|---|---|
| Sperf | `min(TPS / 15.0, 1.0) * 100` | **30.40** (4.56 t/s) |
| Seff | `max(0, (7.0 - peak_rss_gb) / 7.0) * 100` | **5.57** (6.61 GB peak RSS) |
| Pthermal | `10 if throttled else 0` | **0** |
| Sacc | judged on validation set | 0 (placeholder) |
| **Stotal** | `0.50*Sacc + 0.30*Sperf + 0.20*Seff − Pthermal` | **10.23** |

Peak RSS across three separate container-constrained runs ranged 6678.78–
6903.46 MB (3.7%–6.8% margin against the 7GB ceiling); the CPU-pinned run
above (6768.85 MB, 5.57%) sits in the middle of that range and is the most
reference-faithful single measurement.

### Why these numbers, and what I trade

A 7B Q4_K_M model is the largest that fits the 8 GB budget. Measured under a
real enforced 7GB-class container ceiling, peak RSS sits around 6.6-6.9 GB
depending on the run — comfortable, but Seff (5.57) still stays modest
relative to what a much smaller model could claim. That's a deliberate trade,
not an oversight: Sacc carries 50% of the total score and Seff only 20%, and
in this domain accuracy is the difference between working payments code and
a security incident (see the 1.5B ablation below). I spend the memory
budget where the scoring — and the user — put the weight.

The 33.5 s first-token figure is the profiler's fully cold measurement: it
includes mapping the 4.68 GB model file from disk and evaluating a 512-token
prompt. In interactive use the model file stays in the OS page cache after
the first query, which removes the load component; time to first token then
scales with prompt length. Generation speed is unchanged either way (4.82 t/s).

### The 1.5B ablation — measuring the trade instead of asserting it

The strongest formula play in this competition is a small model. I tested
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

On everything the formula measures, the 1.5B wins by ~35 points. I rejected
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
registered prompts is a fast, memory-efficient way to ship broken code. I
submit the 7B: the memory budget is spent where both the scoring weights
(Sacc = 50 %) and the user's safety put it.

These are self-reported development benchmarks. Official scores are measured
by the ADTC profiler on the standard evaluation machine.

### Sustained-load thermal test (native hardware, Aug 4 2026)

The profiler's own `cpu_thermal.core_temp_c_peak` reads `null` in every run
I produced (participant, audit, container, CPU-pinned) — expected, per the
tool's own source: cloud VMs don't expose host thermal sensors, so the
official audit environment can't observe temperature either. To get a real
answer anyway, I ran a ~9-minute sustained load (`llama-bench`, 6 reps ×
300 generated tokens) directly on the native dev laptop with
[LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
polling CPU package temperature every 3 seconds.

- **Peak: 91°C**, a brief spike in the first ~10 seconds (likely the initial
  512-token prompt-processing burst). It then settled and held stable at
  **70-76°C** for the remaining ~9 minutes under continuous load.
- Peak CPU load: 93.5%.
- This sits below the profiler's own throttle-detection threshold (95°C,
  see `thermal.py`), so `throttled` would report `false` even if this
  environment could observe it.
- Full log: `artifacts/thermal_log.csv`.

Included as supplementary evidence the machine doesn't overheat under real
sustained use, separate from the schema's `cpu_thermal` block (which reports
`null`/`false` structurally, not because nothing was tested).

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

**Hybrid dense + BM25 retrieval, and why a prompt-only fix wasn't enough.**
Corpus stress-testing (see Benchmarks) found a real hallucination bug:
same-domain, wrong-provider chunks (e.g. Monnify content retrieved for an
Interswitch question) scored *higher* cosine similarity than a correct
out-of-domain decline — 0.712 vs 0.691 — so a similarity threshold couldn't
distinguish them. The first fix was prompt-level (explicit named-entity
grounding in the system prompt), measured at 3/5 fabricating → 0/5 on one
run against five out-of-corpus providers (Kuda, PalmPay, Interswitch, Paga,
OPay).

That single run wasn't the full story. An independent re-run of the same
five prompts, three repetitions each (15 trials), found the prompt-level fix
was reliable but *not uniformly*: Interswitch, Paga, and OPay held at 9/9
(100%), while Kuda and PalmPay came back at 1/3 and 0/3 — 10/15 (66.7%)
overall. The system prompt's chat call runs at `temperature=0.2` with no
fixed seed, so any single run is a draw, not a guarantee — and Kuda/PalmPay
draw badly because their webhook-verification content is near-identical in
shape to Paystack/Monnify's (retrieved chunks clustered at cosine sim
0.654–0.676, the tightest, most confusable band I measured), giving the
model the most temptation to substitute exactly where grounding matters
most.

Fix: a deterministic pre-generation gate (`scripts/query.py`,
`check_provider_gate`) — a curated list of ~25 known non-corpus African
fintech/banking brand names, matched by word boundary against the incoming
question. If a listed out-of-corpus provider is named and no in-corpus
provider is also named, the question is declined before retrieval or
generation ever run — zero dependency on sampling temperature or seed.
Re-verified independently: 15/15 (100%) across all five original adversarial
prompts, near-instant (no embedding call, no chat call). Regression-checked
clean: in-corpus prompts still route through retrieval + generation normally
(tp_001 unaffected), and a genuine comparison question naming both an
in-corpus and out-of-corpus provider ("How does Kuda compare to Paystack for
webhook handling?") correctly falls through to the softer prompt-level
instruction instead of being blanket-refused.

**Known residual limitation:** the deterministic gate only covers providers
enumerated in advance. A brand not on that list still depends on the
prompt-level instruction — the same mechanism measured at 100% for
Interswitch/Paga/OPay but only 33%/0% for Kuda/PalmPay before the gate
existed. Hybrid dense + BM25 retrieval remains the architecturally general
fix (a query naming any provider absent from the corpus, known or not, would
score near-zero on keyword match, catching the mismatch before generation
instead of after enumeration). Deferred past this submission for the same
reason as before — new dependency, re-tuned scoring path, RAM margin
(3.7–6.8% against the 7GB ceiling) not worth risking for a benefit the
enumerable gate already covers for every provider actually tested.

**Independent validation beyond my own test set.** All 20 adversarial
prompts above were self-authored. To check the fix generalizes, not just
passes my own test, I ran one real, independently-sourced question:
two unconnected developers (on X and Reddit) separately hit the same
real pain point — making a Paystack webhook handler idempotent — neither
prompt written by me. Top-retrieved chunk was Monnify (sim 0.716, higher
than any Paystack chunk), the same shape of mismatch that caused the
original bug. The model correctly stayed on Paystack, gave the right
fix (verify signature, then track processed event IDs), and cited only
real, retrieved sources.
