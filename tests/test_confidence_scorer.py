"""
Tests for core/confidence_scorer.py.
"""
from core.confidence_scorer import compute_confidence


def test_clarification_needed_forces_low():
    result = compute_confidence(
        sql_valid=True, execution_success=True, row_count=5,
        clarification_needed="Did you mean X?",
    )
    assert result.score == 40
    assert result.level == "LOW"


def test_invalid_sql_forces_low():
    result = compute_confidence(sql_valid=False, execution_success=True, row_count=5)
    assert result.score == 10
    assert result.level == "LOW"


def test_execution_failure_forces_low():
    result = compute_confidence(sql_valid=True, execution_success=False, row_count=0)
    assert result.score == 10
    assert result.level == "LOW"


def test_ungrounded_numbers_forces_low_regardless_of_other_inputs():
    result = compute_confidence(
        sql_valid=True, execution_success=True, row_count=100, retries=0,
        resolved_entities=[{"type": "counterparty", "raw": "Amazon", "similarity": 100, "canonical": "Amazon"}],
        numbers_grounded=False,
    )
    assert result.score == 30
    assert result.level == "LOW"


def test_first_try_success_high_similarity_rows_present_is_high():
    result = compute_confidence(
        sql_valid=True, execution_success=True, row_count=10, retries=0,
        resolved_entities=[{"type": "counterparty", "raw": "Amazon", "similarity": 100, "canonical": "Amazon"}],
    )
    assert result.score == 100
    assert result.level == "HIGH"
    assert any("first attempt" in r for r in result.reasons)
    assert any("High-confidence entity match: Amazon" in r for r in result.reasons)


def test_retries_reduce_score_by_15_each():
    result = compute_confidence(sql_valid=True, execution_success=True, row_count=1, retries=2)
    assert result.score == 70
    assert any("2 auto-repair iteration(s)" in r for r in result.reasons)


def test_fuzzy_entity_match_reduces_score_by_10():
    result = compute_confidence(
        sql_valid=True, execution_success=True, row_count=1, retries=0,
        resolved_entities=[{"type": "counterparty", "raw": "Amazn", "similarity": 80}],
    )
    assert result.score == 90
    assert any("Amazn" in r and "80% match" in r for r in result.reasons)


def test_zero_rows_reduces_score():
    result = compute_confidence(sql_valid=True, execution_success=True, row_count=0, retries=0)
    assert result.score == 80
    assert any("0 rows" in r for r in result.reasons)


def test_zero_rows_penalty_floors_at_50():
    # retries=2 -> 100-30=70, then row_count=0 -> max(70-20, 50) == 50
    result = compute_confidence(sql_valid=True, execution_success=True, row_count=0, retries=2)
    assert result.score == 50


def test_level_boundaries():
    assert compute_confidence(sql_valid=True, execution_success=True, row_count=1, retries=0).level == "HIGH"
    assert compute_confidence(sql_valid=True, execution_success=True, row_count=1, retries=1).level == "HIGH"  # 85
    assert compute_confidence(sql_valid=True, execution_success=True, row_count=1, retries=2).level == "MEDIUM"  # 70
    assert compute_confidence(sql_valid=True, execution_success=True, row_count=0, retries=2).level == "LOW"  # 50
