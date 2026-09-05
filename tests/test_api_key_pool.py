"""
Tests for core/api_key_pool.py's ApiKeyPool — the "sticky until failure"
rotation used to fail over across several free-tier Groq keys.
"""
from core.api_key_pool import ApiKeyPool


def test_empty_pool_is_falsy_and_has_no_current_key():
    pool = ApiKeyPool([], name="groq")
    assert not pool
    assert len(pool) == 0
    assert pool.current() == ""


def test_single_key_pool():
    pool = ApiKeyPool(["key-a"], name="groq")
    assert bool(pool)
    assert pool.current() == "key-a"


def test_stays_on_current_key_across_repeated_calls():
    pool = ApiKeyPool(["key-a", "key-b", "key-c"], name="groq")
    assert pool.current() == "key-a"
    assert pool.current() == "key-a"  # calling current() again doesn't rotate


def test_advance_moves_to_next_key():
    pool = ApiKeyPool(["key-a", "key-b", "key-c"], name="groq")
    pool.advance()
    assert pool.current() == "key-b"
    pool.advance()
    assert pool.current() == "key-c"


def test_advance_wraps_around():
    pool = ApiKeyPool(["key-a", "key-b"], name="groq")
    pool.advance()
    assert pool.current() == "key-b"
    pool.advance()
    assert pool.current() == "key-a"


def test_blank_and_whitespace_keys_are_filtered_out():
    pool = ApiKeyPool(["key-a", "", "  ", "key-b"], name="groq")
    assert len(pool) == 2
    assert pool.keys == ["key-a", "key-b"]
