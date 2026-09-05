"""
FastAPI Application — Financial AI Chatbot Backend
===================================================
REST API exposing the Text-to-SQL pipeline with endpoints for
querying, schema inspection, CSV export, and session management.

Endpoints:
    POST /api/query     — Submit a natural language question
    GET  /api/health    — Health check with DB stats
    GET  /api/schema    — Database schema information
    GET  /api/history   — Conversation history for a session
    POST /api/export    — Export query results as CSV
    DELETE /api/history — Clear conversation history
"""
import sys
import io
import csv
import logging
from datetime import date
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    DUCKDB_PATH, LLM_PROVIDER,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEY, GROQ_MODEL,
    MAX_SQL_RETRIES, FUZZY_MATCH_THRESHOLD, LLM_TEMPERATURE,
    LLM_MAX_TOKENS, APP_HOST, APP_PORT,
)
from api.models import (
    QueryRequest, QueryResponse, QueryResultData,
    ExportRequest, HealthResponse, SchemaInfo, SchemaResponse,
    HistoryTurn, HistoryResponse, AnomalyInfo, ConfidenceInfo,
)
from core.sql_generator import SQLGenerator
from core.query_engine import QueryEngine
from core.response_synthesizer import synthesize_response
from core.anomaly_detector import AnomalyDetector
from core.confidence_scorer import compute_confidence

logger = logging.getLogger(__name__)

# ── Session storage (in-memory for hackathon) ──
_sessions: dict[str, SQLGenerator] = {}


def _get_llm_model() -> str:
    """Get the active LLM model name based on provider."""
    return {
        "openrouter": OPENROUTER_MODEL,
        "ollama": OLLAMA_MODEL,
        "openai": OPENAI_MODEL,
        "groq": GROQ_MODEL,
    }.get(LLM_PROVIDER, OPENROUTER_MODEL)


def _get_generator(session_id: str = "default") -> SQLGenerator:
    """Get or create a SQLGenerator for the given session."""
    if session_id not in _sessions:
        _sessions[session_id] = SQLGenerator(
            db_path=DUCKDB_PATH,
            llm_provider=LLM_PROVIDER,
            llm_model=_get_llm_model(),
            ollama_base_url=OLLAMA_BASE_URL,
            openrouter_api_key=OPENROUTER_API_KEY,
            openrouter_base_url=OPENROUTER_BASE_URL,
            openai_api_key=OPENAI_API_KEY,
            groq_api_key=GROQ_API_KEY,
            max_retries=MAX_SQL_RETRIES,
            fuzzy_threshold=FUZZY_MATCH_THRESHOLD,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
    return _sessions[session_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    # Startup
    logger.info("Starting Financial AI Chatbot API...")
    logger.info("Database: %s", DUCKDB_PATH)
    logger.info("LLM Provider: %s, Model: %s", LLM_PROVIDER, _get_llm_model())

    # Verify database exists
    if not Path(DUCKDB_PATH).exists():
        logger.error("Database not found: %s. Run 'python db/init_db.py' first.", DUCKDB_PATH)

    yield

    # Shutdown
    _sessions.clear()
    logger.info("API shutdown complete.")


# ── FastAPI App ──
app = FastAPI(
    title="Financial AI Chatbot",
    description="Deterministic Text-to-SQL engine for financial data analysis. "
                "Ask questions in plain English and get precise, auditable answers "
                "backed by SQL execution against DuckDB.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENDPOINTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Submit a natural language question about financial data.

    The pipeline will:
    1. Resolve entities (vendors, dates) from your query
    2. Generate SQL using the LLM
    3. Validate SQL (read-only, correct tables)
    4. Execute against DuckDB
    5. Synthesize a natural language answer

    Multi-turn: Use the same session_id for follow-up questions.
    """
    generator = _get_generator(request.session_id)

    try:
        result = generator.generate(
            user_query=request.query,
            reference_date=date.today(),
            dry_run=request.dry_run,
        )
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

    # Build response
    response = QueryResponse(
        user_query=result.user_query,
        resolved_entities=result.resolved_entities,
        extracted_sql=result.extracted_sql,
        sql_valid=result.sql_valid,
        validation_error=result.validation_error,
        clarification_needed=result.clarification_needed,
        llm_provider=result.llm_provider,
        llm_model=result.llm_model,
        total_time_ms=result.total_time_ms,
        retries=result.retries,
        error=result.error,
    )

    # Add query result if available
    if result.query_result:
        response.query_result = QueryResultData(
            success=result.query_result.success,
            columns=result.query_result.columns,
            rows=result.query_result.to_dict()["rows"],
            row_count=result.query_result.row_count,
            execution_time_ms=result.query_result.execution_time_ms,
            sql=result.query_result.sql,
            error=result.query_result.error,
            tables_touched=result.query_result.tables_touched,
        )

        # Statistical Anomaly Detection
        if result.query_result.success and not request.dry_run:
            try:
                detector = AnomalyDetector(DUCKDB_PATH)
                anom_alerts = detector.detect_anomalies(
                    query_columns=result.query_result.columns,
                    query_rows=result.query_result.rows,
                    tables_touched=result.query_result.tables_touched,
                )
                response.anomalies = [
                    AnomalyInfo(
                        transaction_id=a.transaction_id,
                        vendor_name=a.vendor_name,
                        amount=a.amount,
                        historical_mean=a.historical_mean,
                        historical_std=a.historical_std,
                        threshold=a.threshold,
                        percentage_above_mean=a.percentage_above_mean,
                        message=a.message,
                    )
                    for a in anom_alerts
                ]
            except Exception as e:
                logger.warning("Anomaly detection failed: %s", e)

        # Synthesize natural language answer
        if result.query_result.success and not request.dry_run:
            try:
                answer = synthesize_response(
                    user_query=request.query,
                    sql=result.extracted_sql,
                    query_result=result.query_result.to_dict(),
                    llm_provider=LLM_PROVIDER,
                    llm_model=_get_llm_model(),
                    ollama_base_url=OLLAMA_BASE_URL,
                    openrouter_api_key=OPENROUTER_API_KEY,
                    openrouter_base_url=OPENROUTER_BASE_URL,
                    openai_api_key=OPENAI_API_KEY,
                    groq_api_key=GROQ_API_KEY,
                    temperature=0.3,
                    max_tokens=512,
                )
                response.answer = answer
            except Exception as e:
                logger.warning("Synthesis failed: %s", e)
                # Fall back to summary
                response.answer = result.query_result.summary_text()
    elif result.clarification_needed:
        response.answer = result.clarification_needed

    # Confidence assessment
    exec_ok = bool(result.query_result and result.query_result.success)
    r_count = result.query_result.row_count if result.query_result else 0
    resolved_list = []
    if result.resolved_entities:
        if "vendor" in result.resolved_entities:
            v = result.resolved_entities["vendor"]
            if isinstance(v, dict):
                resolved_list.append({
                    "type": "vendor",
                    "raw": v.get("raw"),
                    "canonical": v.get("name"),
                    "similarity": v.get("match_score", 100),
                })

    conf = compute_confidence(
        sql_valid=result.sql_valid,
        execution_success=exec_ok,
        row_count=r_count,
        retries=result.retries,
        resolved_entities=resolved_list,
        clarification_needed=result.clarification_needed,
    )
    response.confidence = ConfidenceInfo(
        score=conf.score,
        level=conf.level,
        reasons=conf.reasons,
    )

    return response


@app.get("/api/health", response_model=HealthResponse)
async def health():
    """
    Health check — verifies database connectivity and returns stats.
    """
    try:
        engine = QueryEngine(DUCKDB_PATH)

        # Get table count and total rows
        table_result = engine.execute("""
            SELECT COUNT(*) AS cnt FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'BASE TABLE'
        """)
        table_count = table_result.rows[0][0] if table_result.success else 0

        row_result = engine.execute("""
            SELECT
                (SELECT COUNT(*) FROM transactions) +
                (SELECT COUNT(*) FROM vendor_payouts) +
                (SELECT COUNT(*) FROM reconciliation_status) +
                (SELECT COUNT(*) FROM chart_of_accounts) +
                (SELECT COUNT(*) FROM vendor_list) AS total
        """)
        total_rows = row_result.rows[0][0] if row_result.success else 0

        return HealthResponse(
            status="ok",
            database="connected",
            tables=table_count,
            total_rows=total_rows,
            llm_provider=LLM_PROVIDER,
            llm_model=_get_llm_model(),
        )
    except Exception as e:
        return HealthResponse(
            status="error",
            database=f"error: {str(e)}",
            llm_provider=LLM_PROVIDER,
            llm_model=_get_llm_model(),
        )


@app.get("/api/schema", response_model=SchemaResponse)
async def schema():
    """
    Get database schema information — tables, columns, and row counts.
    """
    try:
        engine = QueryEngine(DUCKDB_PATH)
        table_info = engine.get_table_info()

        tables = []
        for table_name, columns in table_info.items():
            # Get row count
            count_result = engine.execute(f"SELECT COUNT(*) FROM {table_name}")
            row_count = count_result.rows[0][0] if count_result.success else 0

            tables.append(SchemaInfo(
                table_name=table_name,
                columns=[
                    {"name": c["name"], "type": c["type"], "nullable": c["nullable"]}
                    for c in columns
                ],
                row_count=row_count,
            ))

        # Get views
        view_result = engine.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main' AND table_type = 'VIEW'
            ORDER BY table_name
        """)
        views = [row[0] for row in view_result.rows] if view_result.success else []

        return SchemaResponse(tables=tables, views=views)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema error: {str(e)}")


@app.get("/api/history", response_model=HistoryResponse)
async def history(session_id: str = "default"):
    """
    Get conversation history for a session.
    """
    generator = _get_generator(session_id)
    turns = [
        HistoryTurn(
            query=turn["query"],
            sql=turn["sql"],
            summary=turn["summary"],
        )
        for turn in generator.conversation_history
    ]
    return HistoryResponse(session_id=session_id, turns=turns)


@app.delete("/api/history")
async def clear_history(session_id: str = "default"):
    """
    Clear conversation history for a session.
    """
    if session_id in _sessions:
        _sessions[session_id].conversation_history.clear()
        return {"status": "cleared", "session_id": session_id}
    return {"status": "no_session", "session_id": session_id}


@app.post("/api/export")
async def export_csv(request: ExportRequest):
    """
    Execute a SQL query and return results as a downloadable CSV file.
    """
    from core.sql_validator import validate_sql

    # Validate the SQL first
    validation = validate_sql(request.sql)
    if not validation.is_valid:
        raise HTTPException(
            status_code=400,
            detail=f"SQL validation failed: {validation.error}"
        )

    try:
        engine = QueryEngine(DUCKDB_PATH)
        result = engine.execute(request.sql)

        if not result.success:
            raise HTTPException(status_code=400, detail=f"Query error: {result.error}")

        # Build CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(result.columns)
        for row in result.rows:
            writer.writerow([str(v) if v is not None else "" for v in row])

        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={request.filename}"
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


@app.get("/api/vendors")
async def list_vendors():
    """
    List all vendors in the database — useful for autocomplete and validation.
    """
    try:
        engine = QueryEngine(DUCKDB_PATH)
        result = engine.execute("""
            SELECT vendor_id, vendor_name, vendor_alias, category
            FROM vendor_list
            WHERE is_active = true
            ORDER BY vendor_name
        """)
        if result.success:
            return {
                "vendors": [
                    {
                        "id": row[0],
                        "name": row[1],
                        "alias": row[2],
                        "category": row[3],
                    }
                    for row in result.rows
                ]
            }
        raise HTTPException(status_code=500, detail="Failed to fetch vendors")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Static UI Mounting ──
STATIC_DIR = PROJECT_ROOT / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Financial AI Chatbot API is running. Visit /docs for Swagger UI."}

