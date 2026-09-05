"""
Tests for /api/query's response.answer branches in api/main.py — every
terminal pipeline state (clarification / validation failure / LLM error /
dry-run / query_result success-or-failure) must set a real answer, never
leave it empty (which would trip static/app.js's "Query executed
successfully." fallback and misreport a failure as a success).

Bug found in production: a combined greeting ("hi, how are you") was
misclassified as a data question (see test_input_classifier.py), fell
through the whole pipeline, and landed in the validation_error case, which
had no answer-setting branch at all — response.answer stayed "".
"""
import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from core.sql_generator import PipelineResult

client = TestClient(api_main.app)


@pytest.fixture(autouse=True)
def clear_sessions():
    api_main._sessions.clear()
    yield
    api_main._sessions.clear()


def _patch_generate(monkeypatch, result: PipelineResult):
    monkeypatch.setattr(api_main.SQLGenerator, "generate", lambda self, **kwargs: result)


def test_validation_error_with_no_query_result_gets_a_real_answer(monkeypatch):
    result = PipelineResult(
        user_query="hi, how are you",
        extracted_sql="Hi! I'm doing well, thanks for asking...",
        sql_valid=False,
        validation_error="SQL syntax error: Invalid expression / Unexpected token.",
    )
    _patch_generate(monkeypatch, result)

    resp = client.post("/api/query", json={"query": "hi, how are you", "session_id": "s1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"], "answer must not be empty when validation failed"
    assert "valid SQL query" in data["answer"]


def test_llm_error_gets_a_real_answer(monkeypatch):
    result = PipelineResult(user_query="anything", error="LLM call failed: connection timeout")
    _patch_generate(monkeypatch, result)

    resp = client.post("/api/query", json={"query": "anything", "session_id": "s2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"], "answer must not be empty when the LLM call itself failed"
    assert "connection timeout" in data["answer"]


def test_dry_run_gets_a_real_answer(monkeypatch):
    result = PipelineResult(user_query="show me spend", extracted_sql="[DRY RUN]", llm_provider="dry_run")
    _patch_generate(monkeypatch, result)

    resp = client.post("/api/query", json={"query": "show me spend", "session_id": "s3", "dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"], "answer must not be empty on a dry run"
    assert "Dry run" in data["answer"]
