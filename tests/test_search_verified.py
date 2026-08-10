"""Generated from a spec-verify pass against scripts/query.py's search():

    Given a corpus containing chunks from multiple sources
    When a query is run with --source paystack
    Then only chunks whose source is paystack are returned, ranked by similarity descending
    [ASSUMPTION: source filtering is an exact string match against the
     `source` column, applied per-row during ranking]

The real risk this guards: the filter condition is a single `!=` comparison
inside the ranking loop (search(), scripts/query.py). Get that comparison's
polarity wrong and --source paystack silently returns everything *except*
paystack instead of erroring — the kind of bug that passes review because
the function still returns *a* list, just the wrong one. Validated via
mutation testing before being adopted here.
"""
import numpy as np

from scripts.query import search


def test_source_filter_returns_only_matching_source_ranked_by_similarity():
    rows = [
        (1, "paystack", "docs", "paystack chunk A"),
        (2, "flutterwave", "docs", "flutterwave chunk B"),
        (3, "paystack", "docs", "paystack chunk C"),
        (4, "monnify", "docs", "monnify chunk D"),
    ]
    matrix = np.array(
        [
            [1.0, 0.0],   # id1 paystack  -- highest sim to query
            [0.9, 0.1],   # id2 flutterwave -- higher sim than id3, wrong source
            [0.8, 0.2],   # id3 paystack
            [0.0, 1.0],   # id4 monnify
        ],
        dtype=np.float32,
    )
    query_vec = np.array([1.0, 0.0], dtype=np.float32)

    hits = search(query_vec, matrix, rows, top_k=10, source_filter="paystack")

    sources = [src for _score, _cid, src, _cat, _content in hits]
    ids = [cid for _score, cid, *_rest in hits]

    assert sources == ["paystack", "paystack"]
    assert ids == [1, 3]  # id1 (sim=1.0) ranked above id3 (sim=0.970), flutterwave/monnify excluded
