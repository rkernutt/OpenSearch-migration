"""Unit tests for ID sampling query construction (no HTTP)."""
from validate_migration import (
    build_sample_search_body,
    build_stratified_slice_search_body,
    distribute_sample_sizes,
    effective_stratified_slice_count,
)


def test_build_sample_head() -> None:
    b = build_sample_search_body(10, "head")
    assert b["size"] == 10
    assert b["_source"] is False
    assert b["sort"] == ["_doc"]
    assert b["query"] == {"match_all": {}}


def test_build_sample_random_with_seed() -> None:
    b = build_sample_search_body(5, "random", 12345)
    assert b["size"] == 5
    assert b["_source"] is False
    assert "sort" not in b
    assert b["query"]["function_score"]["random_score"]["seed"] == 12345


def test_build_sample_random_default_seed() -> None:
    b = build_sample_search_body(3, "random", None)
    assert b["query"]["function_score"]["random_score"]["seed"] == 42


def test_invalid_mode_raises() -> None:
    try:
        build_sample_search_body(1, "invalid")
    except ValueError as e:
        assert "unknown sample mode" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")


def test_distribute_sample_sizes() -> None:
    assert distribute_sample_sizes(10, 3) == [4, 3, 3]
    assert distribute_sample_sizes(5, 5) == [1, 1, 1, 1, 1]
    assert sum(distribute_sample_sizes(100, 8)) == 100


def test_effective_stratified_slice_count() -> None:
    assert effective_stratified_slice_count(None, 100) == 8
    assert effective_stratified_slice_count(20, 5) == 5
    assert effective_stratified_slice_count(3, 100) == 3


def test_stratified_slice_body() -> None:
    b = build_stratified_slice_search_body(4, 1, 5, 99)
    assert b["size"] == 4
    assert b["slice"] == {"id": 1, "max": 5}
    assert b["query"]["function_score"]["random_score"]["seed"] == 99


def test_distribute_invalid() -> None:
    try:
        distribute_sample_sizes(5, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
