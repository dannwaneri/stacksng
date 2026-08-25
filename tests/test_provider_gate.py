"""Regression tests for check_provider_gate() in scripts/query.py.

Given a question naming a provider not covered by the corpus
When check_provider_gate() runs before retrieval or generation
Then the question is declined deterministically, independent of the LLM

The gate replaced a system-prompt instruction measured at only 33%/0%
reliable for two providers (see REPORT.md) — but a flat word-boundary
denylist carries its own risk, demonstrated twice by a DEV.to reader
(Heinrich Neb, 2026-08-21) within hours of publication:

1. Role confusion: a listed name is genuinely present but names the
   customer's bank, not the API being asked about ("a customer with a
   Wema Bank account"). Fixed by removing plain commercial/settlement
   banks from the denylist, keeping only genuine competing providers.
2. Lexical collision: a brand name is also an ordinary English word
   ("carbon copy", "racing stripe"). Fixed with a collocation-stripping
   pass in _find_provider_mentions() before the word-boundary match runs.

Both fixes are re-encoded here as permanent regression tests, per
Heinrich's point that the gate itself had zero tests protecting it —
the mutation-tested filter in test_search_verified.py was "the only
guarded guard in the repo." This file is the second one.
"""
from scripts.query import check_provider_gate


# --- Registered test prompts and the original adversarial set must still
#     work exactly as documented in REPORT.md. Any change to the provider
#     lists that breaks these is a regression, not a refinement. ---

def test_registered_prompts_pass_through():
    assert check_provider_gate("How do I verify a Paystack webhook signature in Node.js?") is None
    assert check_provider_gate(
        "How do I initialize a Flutterwave payment and handle the redirect callback in Python?"
    ) is None


def test_original_adversarial_providers_still_decline():
    for provider in ["Kuda Bank", "PalmPay", "Interswitch", "Paga", "OPay"]:
        msg = check_provider_gate(f"How do I verify a {provider} webhook signature in Node.js?")
        assert msg is not None, f"{provider} should still decline"


# --- Failure mechanism 1: role confusion (Heinrich Neb, finding #1) ---

def test_settlement_bank_named_as_incidental_context_does_not_decline():
    """A listed name present in the question, but as the customer's bank,
    not the provider whose API is being asked about — must not decline."""
    cases = [
        "How do I implement BVN verification for a customer with a Wema Bank account?",
        "How do I set up a dedicated virtual account for a customer at Access Bank?",
        "How do I format an amount in kobo for a bank transfer to GTBank?",
        "How do Termii and 9PSB work together for USSD banking?",
    ]
    for q in cases:
        assert check_provider_gate(q) is None, f"false decline: {q}"


def test_genuine_competing_provider_still_declines():
    """Kuda stays gated — unlike a settlement bank, asking about Kuda's own
    USSD/API implementation is the dominant reading of the question."""
    msg = check_provider_gate("How do I handle USSD payment flows for a Kuda Bank account?")
    assert msg is not None


# --- Failure mechanism 2: lexical collision (Heinrich Neb, finding #2) ---

def test_ordinary_word_use_of_ambiguous_brand_names_does_not_decline():
    cases = [
        "How do I add a carbon copy recipient to my transactional emails?",
        "How do I calculate the carbon footprint of my API calls?",
        "How do I add a racing stripe design to my checkout page?",
        "How do I read a magnetic stripe card without any payment provider involved?",
    ]
    for q in cases:
        assert check_provider_gate(q) is None, f"false decline: {q}"


def test_genuine_ambiguous_brand_mention_still_declines():
    """The collocation strip must not blind the gate to a real mention."""
    assert check_provider_gate("How do I integrate Carbon's loan API for credit scoring?") is not None
    assert check_provider_gate("How do I set up a Stripe payment intent in Node.js?") is not None


def test_comparison_question_naming_both_falls_through_to_retrieval():
    """An in-corpus provider named alongside an ambiguous/out-of-corpus one
    is a legitimate comparison question, not a pure out-of-scope ask."""
    assert check_provider_gate("How do I compare Paystack webhooks to Stripe webhooks?") is None
