"""
Data Bounds — the dataset's own "as of" date
=============================================
Relative-date questions ("last month", "this quarter") must resolve
against the data's actual date range, not wall-clock today — a demo
dataset frozen in the past (or a real export that stops last Tuesday)
would otherwise silently return empty results for the most natural
follow-up questions. Cached per db_path+mtime so this is one query per
process, not one per request.
"""
import logging
from datetime import date
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, date]] = {}


def get_reference_date(db_path: str) -> date:
    """Return MAX(transaction_date) from the data, falling back to today."""
    try:
        mtime = Path(db_path).stat().st_mtime
    except OSError:
        return date.today()

    cached = _cache.get(db_path)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        con = duckdb.connect(db_path, read_only=True)
        row = con.execute("SELECT MAX(transaction_date) FROM transaction").fetchone()
        con.close()
        max_date = row[0].date() if row and row[0] else date.today()
    except Exception as e:
        logger.warning("Could not determine data max date from %s: %s", db_path, e)
        max_date = date.today()

    _cache[db_path] = (mtime, max_date)
    return max_date


def get_date_range(db_path: str) -> tuple[date | None, date | None]:
    """Return (MIN(transaction_date), MAX(transaction_date)) as dates, or (None, None)."""
    try:
        con = duckdb.connect(db_path, read_only=True)
        row = con.execute("SELECT MIN(transaction_date), MAX(transaction_date) FROM transaction").fetchone()
        con.close()
        if row and row[0] and row[1]:
            return row[0].date(), row[1].date()
    except Exception as e:
        logger.warning("Could not determine data date range from %s: %s", db_path, e)
    return None, None
