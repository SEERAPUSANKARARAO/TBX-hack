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
DUCKDB_PATH = os.getenv("DUCKDB_PATH", str(DB_DIR / "financial.duckdb"))

# ── LLM Settings ──
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
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
TABLE_NAMES = [
    "transactions",
    "vendor_payouts",
    "reconciliation_status",
    "chart_of_accounts",
    "vendor_list",
]

CSV_TO_TABLE_MAP = {
    "chart_of_accounts.csv": "chart_of_accounts",
    "vendor_list.csv": "vendor_list",
    "transactions.csv": "transactions",
    "vendor_payouts.csv": "vendor_payouts",
    "reconciliation_status.csv": "reconciliation_status",
}
