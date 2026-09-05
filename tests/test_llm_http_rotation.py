"""
Tests for core/llm_http.py's post_with_key_rotation — rotates to the next
API key on 429/401/403, retries the SAME key on a 503 or network error.
Uses a fake httpx-like client so no real network calls happen.
"""
import httpx
import pytest

from core.api_key_pool import ApiKeyPool
from core.llm_http import post_with_key_rotation


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json = json_data or {"ok": True}
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"status {self.status_code}", request=None, response=self)


class FakeClient:
    """Records the Authorization header used on each call; returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, json, headers=None):
        self.calls.append(headers)
        return self._responses.pop(0)


def build_headers(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr("core.llm_http.time.sleep", lambda *_: None)


def test_rotates_to_next_key_on_429_and_succeeds():
    pool = ApiKeyPool(["key-a", "key-b"], name="groq")
    client = FakeClient([FakeResponse(429), FakeResponse(200, {"answer": "hi"})])

    resp = post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool, build_headers=build_headers)

    assert resp.json() == {"answer": "hi"}
    assert client.calls[0]["Authorization"] == "Bearer key-a"
    assert client.calls[1]["Authorization"] == "Bearer key-b"
    assert pool.current() == "key-b"


def test_rotates_on_401_rejected_key():
    pool = ApiKeyPool(["key-a", "key-b"], name="groq")
    client = FakeClient([FakeResponse(401), FakeResponse(200)])

    resp = post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool, build_headers=build_headers)

    assert resp.status_code == 200
    assert client.calls[0]["Authorization"] == "Bearer key-a"
    assert client.calls[1]["Authorization"] == "Bearer key-b"


def test_503_retries_same_key_without_rotating():
    pool = ApiKeyPool(["key-a", "key-b"], name="groq")
    client = FakeClient([FakeResponse(503), FakeResponse(200)])

    resp = post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool, build_headers=build_headers)

    assert resp.status_code == 200
    assert client.calls[0]["Authorization"] == "Bearer key-a"
    assert client.calls[1]["Authorization"] == "Bearer key-a"  # same key both times
    assert pool.current() == "key-a"  # never rotated


def test_all_keys_exhausted_raises():
    pool = ApiKeyPool(["key-a", "key-b"], name="groq")
    client = FakeClient([FakeResponse(429)] * 20)

    with pytest.raises(httpx.HTTPStatusError):
        post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool,
                                build_headers=build_headers, max_rounds=4)


def test_success_on_first_try_makes_only_one_call():
    pool = ApiKeyPool(["key-a"], name="groq")
    client = FakeClient([FakeResponse(200)])

    post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool, build_headers=build_headers)

    assert len(client.calls) == 1


def test_empty_pool_raises_immediately():
    pool = ApiKeyPool([], name="groq")
    client = FakeClient([])

    with pytest.raises(ValueError, match="key pool is empty"):
        post_with_key_rotation(client, "https://example.test", json={}, key_pool=pool, build_headers=build_headers)
