"""
LLM HTTP helper — retry with backoff on 429 / 503, plus multi-key rotation
============================================================================
OpenRouter's shared/free tier enforces a tight per-second request cap that
a live demo or a benchmark run will realistically hit. A transient 429
shouldn't surface as a pipeline error — it should back off and retry a
couple of times first.

post_with_key_rotation additionally supports failing over across several
API keys (see core.api_key_pool.ApiKeyPool) — used for Groq, where a
free-tier account's key gets rate-limited or rejected outright rather than
just slowed down.
"""
import time
import logging
from typing import Callable

import httpx

from core.api_key_pool import ApiKeyPool

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 503}
# Statuses that mean THIS key is the problem (rate-limited or rejected) —
# worth rotating to a different key immediately rather than waiting out a
# backoff on the same one. A 503 stays in RETRYABLE_STATUS only (a
# provider-side outage isn't fixed by switching keys).
ROTATE_ON_STATUS = {429, 401, 403}


def post_with_retry(
    client: httpx.Client,
    url: str,
    json: dict,
    headers: dict | None = None,
    max_attempts: int = 6,
    base_delay: float = 1.5,
) -> httpx.Response:
    """POST with exponential backoff on 429/503. Raises on final failure."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            response = client.post(url, json=json, headers=headers)
        except httpx.TransportError as e:
            last_exc = e
            time.sleep(base_delay * (2 ** attempt))
            continue

        if response.status_code not in RETRYABLE_STATUS:
            response.raise_for_status()
            return response

        if attempt == max_attempts - 1:
            response.raise_for_status()
            return response

        retry_after = response.headers.get("Retry-After")
        delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
        logger.warning("LLM call got %d, retrying in %.1fs (attempt %d/%d)",
                        response.status_code, delay, attempt + 1, max_attempts)
        time.sleep(delay)

    if last_exc:
        raise last_exc
    raise RuntimeError("post_with_retry exhausted attempts without a response")


def post_with_key_rotation(
    client: httpx.Client,
    url: str,
    json: dict,
    key_pool: ApiKeyPool,
    build_headers: Callable[[str], dict],
    max_rounds: int | None = None,
    base_delay: float = 1.5,
) -> httpx.Response:
    """
    POST with automatic failover across a rotating pool of API keys.

    On 429 (rate limited) or 401/403 (rejected key), immediately advances
    to the next key in the pool and retries — no backoff wait until every
    key has been tried once this call; only then does it back off before
    cycling through the pool again. A 503 or network error instead retries
    the SAME key with backoff (a provider-side outage isn't fixed by
    switching keys).

    Args:
        key_pool: the ApiKeyPool to draw keys from.
        build_headers: given a key string, returns the request headers to
            use for that attempt (so the Authorization header can be
            rebuilt for whichever key is current).
    """
    if not key_pool:
        raise ValueError(f"{getattr(key_pool, 'name', 'api')} key pool is empty — no API key configured")

    max_rounds = max_rounds or max(6, len(key_pool) * 2)
    last_response = None
    last_exc = None
    rotations_this_cycle = 0

    for attempt in range(max_rounds):
        headers = build_headers(key_pool.current())
        try:
            response = client.post(url, json=json, headers=headers)
        except httpx.TransportError as e:
            last_exc = e
            time.sleep(base_delay * (2 ** min(attempt, 4)))
            continue

        if response.status_code not in ROTATE_ON_STATUS and response.status_code not in RETRYABLE_STATUS:
            response.raise_for_status()
            return response

        last_response = response

        if response.status_code in ROTATE_ON_STATUS:
            key_pool.advance()
            rotations_this_cycle += 1
            if rotations_this_cycle % len(key_pool) == 0:
                # Cycled through every key in the pool without success —
                # back off before trying the whole pool again.
                delay = base_delay * (2 ** min(rotations_this_cycle // len(key_pool), 4))
                logger.warning(
                    "All %d keys in %s pool rate-limited/rejected — backing off %.1fs",
                    len(key_pool), key_pool.name, delay,
                )
                time.sleep(delay)
        else:
            # 503 on the current key — transient, retry the same key.
            delay = base_delay * (2 ** min(attempt, 4))
            time.sleep(delay)

    if last_response is not None:
        last_response.raise_for_status()
    if last_exc:
        raise last_exc
    raise RuntimeError(f"{key_pool.name} key pool: exhausted all attempts without a response")
