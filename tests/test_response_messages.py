"""
Tests for core/response_synthesizer.py's deterministic (non-LLM) message
paths — the 0-row "no data" message and the query-error message. Both
return before any LLM call, so no network/DB is needed.
"""
from core.response_synthesizer import synthesize_response


def test_zero_rows_generic_message_when_no_context():
    answer, grounded, usage = synthesize_response(
        user_query="How much did we spend?",
        sql="SELECT 1;",
        query_result={"success": True, "row_count": 0, "rows": [], "columns": []},
    )
    assert "couldn't find any matching records" in answer
    assert grounded is True


def test_zero_rows_message_mentions_counterparty_context():
    answer, grounded, usage = synthesize_response(
        user_query="How much did we send to Amazon?",
        sql="SELECT 1;",
        query_result={"success": True, "row_count": 0, "rows": [], "columns": []},
        resolved_entities={"counterparty_name": "Amazon Retail India"},
    )
    assert "Amazon Retail India" in answer


def test_zero_rows_message_mentions_date_range_and_entity_scope():
    answer, grounded, usage = synthesize_response(
        user_query="How much did we send to Amazon in June?",
        sql="SELECT 1;",
        query_result={"success": True, "row_count": 0, "rows": [], "columns": []},
        resolved_entities={
            "counterparty_name": "Amazon Retail India",
            "start_date": "2026-06-01",
            "end_date": "2026-06-30",
        },
        entity_id="cust-123",
    )
    assert "Amazon Retail India" in answer
    assert "2026-06-01" in answer and "2026-06-30" in answer
    assert "selected customer" in answer


def test_query_execution_error_message():
    answer, grounded, usage = synthesize_response(
        user_query="Show me something",
        sql="SELECT 1;",
        query_result={"success": False, "error": "Unknown column 'foo'"},
    )
    assert "encountered an error" in answer
    assert "Unknown column 'foo'" in answer
