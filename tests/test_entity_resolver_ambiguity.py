"""
Tests for core/entity_resolver.py's detect_referential_ambiguity() — the
narrow "this refers to a previous turn that doesn't exist yet" clarification
trigger, used only when a session has no conversation_history (see
core/sql_generator.py's SQLGenerator.generate()).
"""
import pytest

from core.entity_resolver import detect_referential_ambiguity


@pytest.mark.parametrize("query", [
    "Compare that to last quarter",
    "What about them?",
    "Show me the same period for last year",
    "and this month?",
    "How does it compare to last time?",
    "What about it",
])
def test_referential_phrases_are_detected(query):
    assert detect_referential_ambiguity(query) is not None


@pytest.mark.parametrize("query", [
    "How much did we spend on Amazon this month?",
    "Who are our top 5 counterparties by spend?",
    "Show unreconciled transactions over 50000",
    "What is our available balance at HDFC Bank?",
])
def test_ordinary_first_turn_questions_are_not_flagged(query):
    assert detect_referential_ambiguity(query) is None
