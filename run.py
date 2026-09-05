"""
Server Entry Point
==================
Start the Financial AI Chatbot API server.

Usage:
    python run.py
    python run.py --port 8080
    python run.py --reload    # Auto-reload on code changes (dev mode)
"""
import sys
import argparse
from pathlib import Path

# Force UTF-8 output on Windows
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import APP_HOST, APP_PORT, DUCKDB_PATH


def main():
    parser = argparse.ArgumentParser(description="Start the Financial AI Chatbot API")
    parser.add_argument("--host", default=APP_HOST, help="Bind host")
    parser.add_argument("--port", type=int, default=APP_PORT, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    if not Path(DUCKDB_PATH).exists():
        print(f"Database not found at {DUCKDB_PATH} — initializing it now...")
        from db.init_db import init_database
        init_database()

    import uvicorn

    print(f"""
╔══════════════════════════════════════════════════════════╗
║         Financial AI Chatbot — API Server                ║
╠══════════════════════════════════════════════════════════╣
║  Server:  http://{args.host}:{args.port}                       ║
║  Docs:    http://localhost:{args.port}/docs                    ║
║  Health:  http://localhost:{args.port}/api/health               ║
╚══════════════════════════════════════════════════════════╝
""")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
