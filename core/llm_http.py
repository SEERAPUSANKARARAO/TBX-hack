"""
LLM HTTP helper — retry with backoff on 429 / 503
===================================================
OpenRouter's shared/free tier enforces a tight per-second request cap that
a live demo or a benchmark run will realistically hit. A transient 429
shouldn't surface as a pipeline error — it should back off and retry a
couple of times first.
"""
import time
import logging

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 503}


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
