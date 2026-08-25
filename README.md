# StacksNG

**The first offline AI coding assistant built for the African developer stack.**

No internet. No API fees. No cloud dependency. Runs entirely on a standard laptop.

## The problem

Every AI coding assistant was built for a developer in Virginia. They default to Stripe.
They don't know what USSD is. They've never heard of Moniepoint. They assume stable
internet, dollar payments, and AWS.

For a developer in Port Harcourt, Lagos, or anywhere across Nigeria, that's not reality.
The African developer stack is Paystack, Flutterwave, Moniepoint, USSD flows, NGN/kobo
currency handling, and BVN verification. No existing AI coding tool knows this stack
deeply. None of them run offline.

## What it does

Ask StacksNG how to verify a Paystack webhook, handle a Flutterwave bank transfer,
implement USSD flows, or format NGN currency: it answers correctly, with citations,
entirely on-device.

```
$ python scripts/query.py "How do I verify a Paystack webhook signature in Node.js?"
[retrieving context...] [generating answer...]

To verify a Paystack webhook signature in Node.js:

1. Get the signature from the x-paystack-signature header
2. Compute HMAC SHA512 of the request body using your secret key
3. Compare the computed hash to the header value
```

```javascript
const crypto = require('crypto');

const hash = crypto
  .createHmac('sha512', process.env.PAYSTACK_SECRET_KEY)
  .update(JSON.stringify(req.body))
  .digest('hex');

if (hash === req.headers['x-paystack-signature']) {
  // verified
}
```

> Source: https://paystack.com/docs/payments/webhooks#verify-event-origin

## How it works

StacksNG is a RAG (Retrieval-Augmented Generation) system:

1. **Corpus**: 780 chunks scraped from Paystack, Flutterwave, Monnify, and Termii
   official documentation
2. **Embeddings**: each chunk embedded with `nomic-embed-text` via Ollama, stored
   in SQLite
3. **Retrieval**: when you ask a question, the most relevant chunks are found via
   vector similarity search
4. **Generation**: `qwen2.5-coder:7b` generates an answer using the retrieved
   context, citing the source

| Source | Chunks |
|---|---|
| Paystack | 340 |
| Flutterwave | 114 |
| Monnify | 290 |
| Termii | 36 |
| **Total** | **780** |

## Stack

- **Model:** qwen2.5-coder:7b (GGUF, Q4_K_M quantized)
- **Runtime:** Ollama / llama.cpp
- **Embeddings:** nomic-embed-text
- **Storage:** SQLite
- **Language:** Python

## Hardware

Built and tested on an Intel i5 11th Gen with Intel Iris Xe integrated graphics,
16 GB RAM. Targets the ADTC 2026 standard laptop profile (8 GB RAM, integrated
graphics, no discrete GPU).

## Setup

```bash
# Clone the repo
git clone https://github.com/dannwaneri/stacksng
cd stacksng

# Install Ollama and pull the models
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text

# Install Python dependencies
pip install -r requirements.txt

# Run a query
python scripts/query.py "How do I verify a Paystack webhook signature?"
```

## Why this matters

Cloud-hosted LLMs require API fees, stable fiber, and sustained electricity. For a
university student in Lagos, an extension officer in Arusha, or a small-business
owner in Dakar, these are not minor frictions. They are blockers.

StacksNG proves that specialized, accurate AI tooling can run entirely offline on
the hardware people already own, without asking African developers to adapt to
infrastructure built for someone else.

## Built for

[Africa Deep Tech Challenge 2026](https://adtc-2026.devpost.com/): The Laptop LLM
Challenge, Coding Assistants track.

## What's next

- Africa's Talking USSD documentation for deeper offline coverage
- CLI polish and developer experience improvements
- Hybrid dense + BM25 retrieval, the more general fix for entity-grounding
  than the deterministic gate currently covers (see REPORT.md)
- Expansion to Ghana (MTN MoMo), Kenya (M-Pesa), and South Africa (Ozow)

## License

MIT
