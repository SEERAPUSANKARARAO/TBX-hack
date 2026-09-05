"""
Database Connection — single source of truth
===============================================
Every component that talks to MySQL goes through get_connection() rather
than building its own connection params. Changing databases/credentials
means editing config.py (or the env vars behind it) — nothing else.

Two credential sets, deliberately:
  - readonly=True  (finquery_ro, SELECT-only grant): used by every query
    execution path. This is a real MySQL-level read-only guarantee,
    independent of the SQL-validator's SELECT-only check — belt and
    suspenders, enforced by the database itself, not just application code.
  - readonly=False (finquery_app, full privileges): used only by
    db/init_db.py to create schema and load data.
"""
import pymysql
import pymysql.cursors

from config import (
    DB_HOST, DB_PORT, DB_NAME,
    DB_ADMIN_USER, DB_ADMIN_PASSWORD,
    DB_READONLY_USER, DB_READONLY_PASSWORD,
)


def get_connection(readonly: bool = True, **kwargs) -> pymysql.Connection:
    """Open a new MySQL connection. Callers are responsible for closing it."""
    user = DB_READONLY_USER if readonly else DB_ADMIN_USER
    password = DB_READONLY_PASSWORD if readonly else DB_ADMIN_PASSWORD
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=user,
        password=password,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
        autocommit=True,
        **kwargs,
    )
