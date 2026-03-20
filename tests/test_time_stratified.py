"""Pure helpers for time/value bucket sampling (no HTTP)."""
from validate_migration import (
    build_time_bucket_search_body,
    iter_time_bucket_ranges,
)


def test_iter_time_bucket_single_point() -> None:
    r = iter_time_bucket_ranges(5.0, 5.0, 3)
    assert r == [{"gte": 5.0, "lte": 5.0}]


def test_iter_time_bucket_non_overlapping() -> None:
    r = iter_time_bucket_ranges(0.0, 100.0, 4)
    assert len(r) == 4
    assert r[0] == {"gte": 0.0, "lt": 25.0}
    assert r[-1] == {"gte": 75.0, "lte": 100.0}


def test_build_time_bucket_search_body() -> None:
    b = build_time_bucket_search_body(
        3, "@timestamp", {"gte": 1.0, "lt": 2.0}, 99
    )
    assert b["size"] == 3
    assert b["_source"] is False
    assert b["query"]["function_score"]["random_score"]["seed"] == 99
    assert b["query"]["function_score"]["query"]["bool"]["filter"][0]["range"][
        "@timestamp"
    ] == {"gte": 1.0, "lt": 2.0}


def test_iter_invalid_buckets() -> None:
    try:
        iter_time_bucket_ranges(0, 10, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
