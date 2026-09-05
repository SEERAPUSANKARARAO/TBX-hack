"""
Tests for session isolation in api/main.py — the backend contract the
frontend's customer-switch reset (static/app.js resetSessionState()) relies
on: a session_id keys one SQLGenerator instance, its conversation_history
never leaks into another session_id, and clearing one session's history
never touches another's.
"""
import pytest
from fastapi.testclient import TestClient

import api.main as api_main

client = TestClient(api_main.app)


@pytest.fixture(autouse=True)
def clear_sessions():
    """`_sessions` is a module-level dict shared across the whole process —
    reset it around every test so tests can't pollute each other."""
    api_main._sessions.clear()
    yield
    api_main._sessions.clear()


def test_sessions_have_independent_conversation_history():
    gen_a = api_main._get_generator("session-a")
    gen_b = api_main._get_generator("session-b")

    gen_a.conversation_history.append({"query": "q1", "sql": "SELECT 1;", "summary": "s1"})

    assert len(gen_a.conversation_history) == 1
    assert gen_b.conversation_history == []


def test_new_session_starts_with_empty_history_even_if_others_have_data():
    gen_a = api_main._get_generator("session-a")
    gen_a.conversation_history.append({"query": "q1", "sql": "SELECT 1;", "summary": "s1"})

    # Simulates a customer switch minting a brand-new session_id.
    gen_new = api_main._get_generator("session-brand-new")
    assert gen_new.conversation_history == []


def test_get_generator_returns_same_instance_for_same_session_id():
    gen_1 = api_main._get_generator("session-a")
    gen_2 = api_main._get_generator("session-a")
    assert gen_1 is gen_2


def test_clear_history_endpoint_is_scoped_to_one_session():
    gen_a = api_main._get_generator("session-a")
    gen_b = api_main._get_generator("session-b")
    gen_a.conversation_history.append({"query": "q1", "sql": "SELECT 1;", "summary": "s1"})
    gen_b.conversation_history.append({"query": "q2", "sql": "SELECT 2;", "summary": "s2"})

    resp = client.delete("/api/history", params={"session_id": "session-a"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"

    resp_a = client.get("/api/history", params={"session_id": "session-a"})
    assert resp_a.json()["turns"] == []

    resp_b = client.get("/api/history", params={"session_id": "session-b"})
    assert len(resp_b.json()["turns"]) == 1
    assert resp_b.json()["turns"][0]["query"] == "q2"


def test_clear_history_on_unknown_session_does_not_create_one():
    resp = client.delete("/api/history", params={"session_id": "never-seen"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_session"
    assert "never-seen" not in api_main._sessions


def test_query_with_unknown_entity_id_is_rejected(monkeypatch):
    monkeypatch.setattr(api_main, "get_known_entity_ids", lambda: {"real-entity-1", "real-entity-2"})
    resp = client.post("/api/query", json={
        "query": "How much did we spend?",
        "session_id": "session-a",
        "entity_id": "not-a-real-entity",
    })
    assert resp.status_code == 400
    assert "Unknown customer entity_id" in resp.json()["detail"]


def test_query_with_known_entity_id_is_not_rejected_by_the_guardrail(monkeypatch):
    """Doesn't exercise the full pipeline (would need a live LLM/DB) — just
    confirms the entity_id guardrail itself doesn't block a real id, by
    making the pipeline call raise instead of actually running."""
    monkeypatch.setattr(api_main, "get_known_entity_ids", lambda: {"real-entity-1"})

    def _boom(*args, **kwargs):
        raise RuntimeError("pipeline reached — guardrail did not block")

    monkeypatch.setattr(api_main.SQLGenerator, "generate", _boom)
    resp = client.post("/api/query", json={
        "query": "How much did we spend?",
        "session_id": "session-a",
        "entity_id": "real-entity-1",
    })
    # 500 (pipeline error) rather than 400 (guardrail rejection) proves the
    # known entity_id passed the guardrail and reached generate().
    assert resp.status_code == 500


def test_query_entity_guardrail_does_not_block_when_lookup_unavailable(monkeypatch):
    """get_known_entity_ids() returns None (not an empty set) when the DB
    lookup itself failed — the guardrail must not treat that as "no entities
    are known" and reject everything during a transient DB hiccup."""
    monkeypatch.setattr(api_main, "get_known_entity_ids", lambda: None)

    def _boom(*args, **kwargs):
        raise RuntimeError("pipeline reached — guardrail did not block")

    monkeypatch.setattr(api_main.SQLGenerator, "generate", _boom)
    resp = client.post("/api/query", json={
        "query": "How much did we spend?",
        "session_id": "session-a",
        "entity_id": "anything",
    })
    assert resp.status_code == 500
