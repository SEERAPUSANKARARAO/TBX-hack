"""Persistent request telemetry. No prompts, SQL, answers or financial records stored."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get('FINQUERY_ANALYTICS_DB', str(Path(__file__).resolve().parents[1] / 'data' / 'analytics.sqlite3')))

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, session_id TEXT, model TEXT,
        outcome TEXT, duration_ms REAL, database_ms REAL, input_tokens INTEGER,
        output_tokens INTEGER, retries INTEGER, grounding TEXT)''')
    db.execute('CREATE INDEX IF NOT EXISTS requests_time ON requests(timestamp)')
    try:
        with db:
            yield db
    finally:
        db.close()

def record(event):
    with connect() as db:
        db.execute('INSERT INTO requests VALUES (:id,:timestamp,:session_id,:model,:outcome,:duration_ms,:database_ms,:input_tokens,:output_tokens,:retries,:grounding)', event)

def read(days=7):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as db:
        rows = db.execute('SELECT * FROM requests WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 10001', (cutoff,)).fetchall()
    return {'requests': [dict(row) for row in rows[:10000]], 'truncated': len(rows) > 10000,
            'generated_at': datetime.now(timezone.utc).isoformat(), 'timezone': 'UTC'}
