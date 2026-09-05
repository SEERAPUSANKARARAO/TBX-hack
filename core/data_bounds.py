"""
Data Bounds — the dataset's own "as of" date
=============================================
Relative-date questions ("last month", "this quarter") must resolve
against the data's actual date range, not wall-clock today — a demo
dataset frozen in the past (or a real export that stops last Tuesday)
would otherwise silently return empty results for the most natural
follow-up questions. Cached for a short TTL so this isn't one query per
request, but still picks up new data within a minute of a re-init.
"""
import time
import logging
from datetime import date

from core.db_connection import get_connection

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60
_cache: dict[str, tuple[float, object]] = {}


def get_reference_date() -> date:
    """Return MAX(transaction_date) from the data, falling back to today."""
    cached = _cache.get("reference_date")
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]

    try:
        con = get_connection(readonly=True)
        with con.cursor() as cur:
            cur.execute("SELECT MAX(transaction_date) FROM transaction")
            row = cur.fetchone()
        con.close()
        max_date = row[0].date() if row and row[0] else date.today()
    except Exception as e:
        logger.warning("Could not determine data max date: %s", e)
        max_date = date.today()

    _cache["reference_date"] = (time.monotonic(), max_date)
    return max_date


def get_date_range() -> tuple[date | None, date | None]:
    """Return (MIN(transaction_date), MAX(transaction_date)) as dates, or (None, None)."""
    cached = _cache.get("date_range")
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]

    result = (None, None)
    try:
        con = get_connection(readonly=True)
        with con.cursor() as cur:
            cur.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM transaction")
            row = cur.fetchone()
        con.close()
        if row and row[0] and row[1]:
            result = (row[0].date(), row[1].date())
    except Exception as e:
        logger.warning("Could not determine data date range: %s", e)

    _cache["date_range"] = (time.monotonic(), result)
    return result


def get_known_entity_ids() -> set[str] | None:
    """
    Return the set of real entity_ids present in the data — used to guard
    against a spoofed/stale/typo'd entity_id silently scoping every query to
    a customer that doesn't exist (see api/main.py's /api/query handler).

    Returns None (rather than an empty set) when the lookup itself failed —
    callers should treat that as "can't verify, don't block" and let the
    query proceed to fail with a clearer downstream error instead of
    wrongly reporting every entity_id as unknown during a DB hiccup.
    """
    cached = _cache.get("entity_ids")
    if cached and (time.monotonic() - cached[0]) < _TTL_SECONDS:
        return cached[1]

    try:
        con = get_connection(readonly=True)
        with con.cursor() as cur:
            cur.execute("SELECT DISTINCT entity_id FROM account")
            result: set[str] | None = {row[0] for row in cur.fetchall()}
        con.close()
    except Exception as e:
        logger.warning("Could not determine known entity_ids: %s", e)
        result = None

    _cache["entity_ids"] = (time.monotonic(), result)
    return result
