# Technical Report — StacksNG

**Team ID:** 1067588-stacksng
**Domain:** coding_assistants
**Model:** qwen2.5-coder-7b-Q4_K_M

---

## Problem

Nigerian and African developers build on a different stack than the rest of the world.
Paystack, Flutterwave, Moniepoint, USSD flows, NGN/kobo currency handling, and BVN
verification are the primitives of African fintech development. No existing AI coding
tool knows this stack deeply — and all of them require stable internet and API fees
that are blockers for developers across the continent.

StacksNG is an offline AI coding assistant that answers questions about the African
developer stack correctly, with citations, entirely on-device.

Target user: Nigerian and African software developers building fintech integrations.

---

## Design Decisions

- **Base model:** qwen2.5-coder:7b — purpose-built for code generation, strong
  multilingual and non-Western context, fits within 8 GB RAM at Q4_K_M quantization.
- **Quantization:** Q4_K_M — balance of quality and memory footprint. Q8_0 exceeded
  budget; Q2_K degraded code-generation quality unacceptably.
- **Runtime:** llama.cpp via Ollama for development; raw GGUF + llama-bench for the
  profiler submission.
- **RAG layer:** 780 chunks across Paystack (340), Flutterwave (114), Monnify (290),
  Termii (36) — embedded with nomic-embed-text, stored in SQLite. Each chunk carries
  its source URL so every answer can cite the original documentation.
- **Why RAG over fine-tuning:** the corpus is documentation, not instruction pairs.
  RAG gives citation traceability — every answer cites the source URL — and the
  corpus can be refreshed without retraining when an API evolves.

---

## Constraints

- Target: 8 GB RAM, Intel Iris Xe integrated graphics, no discrete GPU.
- Pure CPU inference via llama.cpp.
- No internet dependency — corpus is local SQLite, model is local GGUF.
- Power unreliability in Nigeria — fast first-token latency matters more than
  sustained throughput.

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
| Time to first token | 33,464.86 ms (cold mmap of 4.68 GB model + prompt eval) |
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

A 7B Q4_K_M model is the largest that fits the 8 GB budget. Peak RSS at 6.89 GB
sits ~110 MB under the 7 GB Seff ceiling, so Seff is structurally near zero by
design — ADTC rewards small models on Seff, and we have chosen a larger model
deliberately because the African-fintech domain demands precise code generation
and citation traceability that sub-1B models do not deliver at acceptable quality.

Cold first-token latency is 33.5 s because llama-bench measures from a fully
cold mmap. In the actual product (after a single warmup call) the model stays
resident and first-token latency drops to the low hundreds of milliseconds.

These are self-reported development benchmarks. Official scores are measured by
the ADTC profiler on the standard evaluation machine.
