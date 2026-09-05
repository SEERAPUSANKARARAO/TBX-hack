"""
Central configuration for the Financial AI Chatbot.
Loads settings from .env file with sensible defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ── Paths ──
PROJECT_ROOT = Path(__file__).parent.resolve()
DB_DIR = PROJECT_ROOT / "db"
SAMPLE_DATA_DIR = PROJECT_ROOT / "sample_data"

# ── Database (MySQL) — the single place connection details live.
# Change these (or the env vars behind them) and every component picks it up;
# nothing else in the app hardcodes a connection. ──
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "finquery")
# Full-privilege user — schema/data setup only (db/init_db.py).
DB_ADMIN_USER = os.getenv("DB_ADMIN_USER", "finquery_app")
DB_ADMIN_PASSWORD = os.getenv("DB_ADMIN_PASSWORD", "")
# SELECT-only user — the actual query-execution runtime. A real database-level
# read-only guarantee, not just an application-level check.
DB_READONLY_USER = os.getenv("DB_READONLY_USER", "finquery_ro")
DB_READONLY_PASSWORD = os.getenv("DB_READONLY_PASSWORD", "")

# ── LLM Settings ──
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
# Comma-separated list of Groq API keys (e.g. several free-tier accounts) —
# takes priority over the single GROQ_API_KEY above when set. Rotated
# automatically on a 429/401/403 — see core/api_key_pool.py and
# core/llm_http.py's post_with_key_rotation.
GROQ_API_KEYS = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()] or (
    [GROQ_API_KEY] if GROQ_API_KEY else []
)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openrouter")  # openrouter, ollama, openai, groq
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))

# ── App Settings ──
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# ── Guardrail Thresholds ──
MAX_SQL_RETRIES = int(os.getenv("MAX_SQL_RETRIES", "2"))
ANOMALY_SIGMA_THRESHOLD = float(os.getenv("ANOMALY_SIGMA_THRESHOLD", "2.5"))
FUZZY_MATCH_THRESHOLD = int(os.getenv("FUZZY_MATCH_THRESHOLD", "90"))

# ── Schema Metadata (for prompt injection) ──
# Base tables match the TBX client schema exactly. `transaction_derived`
# is populated locally by db/init_db.py (see core/description_parser.py)
# and is not part of the client's raw export.
TABLE_NAMES = [
    "bank",
    "account",
    "transaction",
    "transaction_derived",
]

CSV_TO_TABLE_MAP = {
    "bank.csv": "bank",
    "account.csv": "account",
    "transaction.csv": "transaction",
}
