"""
Tests for multi-turn conversation memory: how many turns SQLGenerator
stores (core/sql_generator.py) and how many of those actually get injected
into the next prompt (core/prompts.py's build_user_message).
"""
from core.prompts import build_user_message
from core.query_engine import QueryResult
from core.sql_generator import SQLGenerator


def _turn(n):
    return {"query": f"question {n}", "sql": f"SELECT {n};", "summary": f"summary {n}"}


def test_build_user_message_with_no_history():
    msg = build_user_message("How much did we spend?", None)
    assert msg == "Question: How much did we spend?"


def test_build_user_message_includes_all_turns_when_six_or_fewer():
    history = [_turn(i) for i in range(1, 5)]  # 4 turns
    msg = build_user_message("current question", history)
    for i in range(1, 5):
        assert f"User: question {i}\n" in msg
    assert "current question" in msg


def test_build_user_message_only_includes_last_six_of_more():
    history = [_turn(i) for i in range(1, 11)]  # 10 turns
    msg = build_user_message("current question", history)
    for i in range(1, 5):  # turns 1-4 should have been dropped
        assert f"User: question {i}\n" not in msg
    for i in range(5, 11):  # turns 5-10 (the last 6) should be present
        assert f"User: question {i}\n" in msg


def test_conversation_history_capped_at_ten_turns():
    generator = SQLGenerator()
    generator._call_llm = lambda messages: "```sql\nSELECT 1;\n```"
    generator.query_engine.execute = lambda sql: QueryResult(
        success=True, columns=["x"], rows=[[1]], row_count=1, sql=sql,
    )

    for i in range(15):
        generator.generate(f"question {i}", dry_run=False)

    assert len(generator.conversation_history) == 10
    # the oldest 5 turns (0-4) should have been dropped, keeping the last 10 (5-14)
    stored_queries = [turn["query"] for turn in generator.conversation_history]
    assert "question 0" not in stored_queries
    assert "question 14" in stored_queries
