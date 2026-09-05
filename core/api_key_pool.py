"""
Rotating API Key Pool — automatic failover across multiple free-tier keys
===========================================================================
Groq's free tier enforces a strict per-key rate limit. Running several free
accounts side by side and rotating between their keys on a 429 (rate
limited) or 401/403 (rejected) lets a demo/benchmark keep going instead of
stalling on one exhausted key. See core/llm_http.py's post_with_key_rotation
for how a pool is actually used in an HTTP call.
"""
import logging
import threading

logger = logging.getLogger(__name__)


class ApiKeyPool:
    """
    A small rotating pool of API keys with "sticky" behavior: stays on the
    current key across calls until it actually fails, then advances to the
    next one — not a round-robin on every call.
    """

    def __init__(self, keys: list[str], name: str = "api"):
        self.keys = [k.strip() for k in keys if k and k.strip()]
        self.name = name
        self._index = 0
        self._lock = threading.Lock()

    def __bool__(self) -> bool:
        return bool(self.keys)

    def __len__(self) -> int:
        return len(self.keys)

    def current(self) -> str:
        """The currently active key, or "" if the pool is empty."""
        with self._lock:
            return self.keys[self._index] if self.keys else ""

    def current_index(self) -> int:
        with self._lock:
            return self._index

    def advance(self) -> str:
        """
        Call after the current key fails (429/401/403). Moves the pointer
        to the next key in the pool (wrapping around) so future calls try
        it first. Returns the new current key ("" if the pool is empty).
        """
        with self._lock:
            if not self.keys:
                return ""
            self._index = (self._index + 1) % len(self.keys)
            logger.warning(
                "%s key pool: rotating to key #%d/%d", self.name, self._index + 1, len(self.keys),
            )
            return self.keys[self._index]
