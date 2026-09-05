"""
FastAPI Application — Financial AI Chatbot Backend
===================================================
REST API exposing the Text-to-SQL pipeline with endpoints for
querying, schema inspection, CSV export, and session management.
Database is MySQL — see core/db_connection.py for the single place
connection details live.

Endpoints:
    POST /api/query          — Submit a natural language question
    GET  /api/health         — Health check with DB stats
    GET  /api/schema         — Database schema information
    GET  /api/history        — Conversation history for a session
    POST /api/export         — Export query results as CSV
    DELETE /api/history      — Clear conversation history
    GET  /api/counterparties — List extracted counterparty names
    GET  /api/entities       — List customer entity_ids (dropdown; no login in this build)
    GET  /api/accuracy       — Latest benchmark.py accuracy/efficiency summary
"""
import sys
import io
import json
import csv
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    LLM_PROVIDER,
    OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL,
    OLLAMA_BASE_URL, OLLAMA_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL, GROQ_API_KEYS, GROQ_MODEL,
    MAX_SQL_RETRIES, FUZZY_MATCH_THRESHOLD, LLM_TEMPERATURE,
    LLM_MAX_TOKENS, APP_HOST, APP_PORT,
    DB_HOST, DB_PORT, DB_NAME,
)
from core.api_key_pool import ApiKeyPool
from api.models import (
    QueryRequest, QueryResponse, QueryResultData,
    ExportRequest, HealthResponse, SchemaInfo, SchemaResponse,
    HistoryTurn, HistoryResponse, AnomalyInfo, ConfidenceInfo,
)
from core.sql_generator import SQLGenerator
from core import analytics
from core.query_engine import QueryEngine
from core.response_synthesizer import synthesize_response
from core.anomaly_detector import AnomalyDetector
from core.confidence_scorer import compute_confidence
from core.data_bounds import get_reference_date, get_known_entity_ids
from core.sql_validator import VALID_TABLES
from core.db_connection import get_connection

logger = logging.getLogger(__name__)

_sessions: dict[str, SQLGenerator] = {}

# Shared across every session/request so rotation state (which key is
# currently active) persists app-wide, rather than resetting per session.
GROQ_KEY_POOL = ApiKeyPool(GROQ_API_KEYS, name="groq")

# Plain-English labels for the SQL Lineage & Audit Trace card's business-
# readable summary (see _build_lineage_summary) — the raw technical grid
# (tables_touched, columns, etc.) stays untouched alongside this.
FRIENDLY_TABLE_NAMES = {
    "bank": "Bank Master",
    "account": "Account Master",
    "transaction": "Transaction Ledger",
    "transaction_derived": "Extracted Counterparty Data",
    "v_account_enriched": "Account Records (bank-enriched, masked)",
    "v_transaction_enriched": "Transaction Records (bank, account & counterparty enriched, masked)",
    "v_counterparty_spend_summary": "Pre-aggregated Counterparty Spend Summary",
    "v_counterparty_lookup": "Counterparty Directory",
    "v_reconciliation_summary": "Reconciliation Status Summary",
    "v_entity_lookup": "Customer Entity Directory",
}
assert set(FRIENDLY_TABLE_NAMES) == VALID_TABLES, "FRIENDLY_TABLE_NAMES must cover every valid table/view"


def _build_lineage_summary(
    tables_touched: list[str], resolved_entities: dict, entity_id: str | None, row_count: int,
) -> str:
    """One plain-English sentence describing what data an answer came from —
    the business-readable counterpart to the raw technical audit grid."""
    friendly = [FRIENDLY_TABLE_NAMES.get(t, t.replace("_", " ").title()) for t in tables_touched]
    if not friendly:
        return ""

    data_desc = " and ".join(friendly) if len(friendly) <= 2 else ", ".join(friendly[:-1]) + f", and {friendly[-1]}"

    filters = []
    if resolved_entities.get("counterparty_name"):
        filters.append(f"counterparty '{resolved_entities['counterparty_name']}'")
    if resolved_entities.get("bank_name"):
        filters.append(f"bank '{resolved_entities['bank_name']}'")
    if resolved_entities.get("start_date") and resolved_entities.get("end_date"):
        filters.append(f"between {resolved_entities['start_date']} and {resolved_entities['end_date']}")
    if entity_id:
        filters.append("scoped to the selected customer")
    filter_desc = f", filtered to {', '.join(filters)}" if filters else ""

    record_desc = f"{row_count} matching record{'s' if row_count != 1 else ''}"
    return f"This answer was computed from your {data_desc} data{filter_desc}, returning {record_desc}."


def _get_llm_model() -> str:
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
            llm_provider=LLM_PROVIDER,
            llm_model=_get_llm_model(),
            ollama_base_url=OLLAMA_BASE_URL,
            openrouter_api_key=OPENROUTER_API_KEY,
            openrouter_base_url=OPENROUTER_BASE_URL,
            openai_api_key=OPENAI_API_KEY,
            groq_key_pool=GROQ_KEY_POOL,
            max_retries=MAX_SQL_RETRIES,
            fuzzy_threshold=FUZZY_MATCH_THRESHOLD,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
    return _sessions[session_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Financial AI Chatbot API...")
    logger.info("Database: MySQL %s:%s/%s", DB_HOST, DB_PORT, DB_NAME)
    logger.info("LLM Provider: %s, Model: %s", LLM_PROVIDER, _get_llm_model())

    try:
        QueryEngine().execute("SELECT 1")
    except Exception as e:
        logger.error("Database not reachable at startup: %s. Run 'python db/init_db.py' first.", e)

    yield

    _sessions.clear()
    logger.info("API shutdown complete.")


app = FastAPI(
    title="Financial AI Chatbot",
    description="Deterministic Text-to-SQL engine for bank transaction data, backed by MySQL. "
                "Ask questions in plain English and get precise, auditable answers "
                "backed by SQL execution. Account numbers and UTRs "
                "are masked by default — never returned raw.",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _build_resolved_entities_response(resolved_entities: dict) -> dict:
    """Nested shape the frontend renders (see static/app.js)."""
    out: dict = {"counterparty": None, "bank": None, "dates": None, "unresolved_counterparty": None}
    if resolved_entities.get("counterparty_name"):
        out["counterparty"] = {
            "name": resolved_entities["counterparty_name"],
            "match_score": resolved_entities.get("counterparty_match_score", 100),
        }
    if resolved_entities.get("bank_code"):
        out["bank"] = {
            "code": resolved_entities["bank_code"],
            "name": resolved_entities.get("bank_name"),
        }
    if resolved_entities.get("start_date"):
        out["dates"] = {
            "start_date": resolved_entities["start_date"],
            "end_date": resolved_entities.get("end_date"),
        }
    if resolved_entities.get("unresolved_counterparty"):
        out["unresolved_counterparty"] = resolved_entities["unresolved_counterparty"]
    return out


@app.get("/api/analytics")
async def analytics_data(days: int = 7):
    if days not in (1, 7, 30, 90):
        raise HTTPException(status_code=422, detail="days must be 1, 7, 30 or 90")
    try:
        return analytics.read(days)
    except Exception:
        logger.exception("Analytics read failed")
        raise HTTPException(status_code=503, detail="Analytics storage unavailable")


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    started = time.perf_counter()
    event = dict(id=str(uuid.uuid4()), timestamp=datetime.now(timezone.utc).isoformat(),
                 session_id=request.session_id, model=_get_llm_model(), outcome="technical_failure",
                 duration_ms=0, database_ms=None, input_tokens=None, output_tokens=None,
                 retries=0, grounding="not_evaluated")
    try:
        response = await _execute_query(request)
        qr = response.query_result
        event.update(input_tokens=response.prompt_tokens, output_tokens=response.completion_tokens,
                     retries=response.retries, grounding=response.grounding_status)
        if response.direct_response_kind:
            event.update(outcome=response.direct_response_kind, model="No model call")
        elif response.clarification_needed:
            event["outcome"] = "clarification"
        elif qr and qr.success:
            event["outcome"] = "answered" if qr.row_count else "no_matching_data"
        if qr:
            event["database_ms"] = qr.execution_time_ms
        response.total_time_ms = (time.perf_counter() - started) * 1000
        return response
    finally:
        event["duration_ms"] = (time.perf_counter() - started) * 1000
        if not request.dry_run:
            try:
                analytics.record(event)
            except Exception:
                # Observability must never break the customer's answer.
                logger.exception("Analytics write failed")


async def _execute_query(request: QueryRequest):
    """
    Submit a natural language question about bank transaction data.

    The pipeline will:
    1. Resolve entities (counterparty, dates) from your query
    2. Generate SQL using the LLM
    3. Validate SQL (read-only, correct tables, no raw PII columns, entity_id-scoped if selected)
    4. Execute against MySQL
    5. Synthesize a natural language answer, verified against the result

    Multi-turn: Use the same session_id for follow-up questions.
    entity_id: optional — scopes the query to one customer (see /api/entities).
    There is no login in this build, so this is a usability scope, not a security boundary.
    """
    generator = _get_generator(request.session_id)
    reference_date = get_reference_date()

    known_entity_ids = get_known_entity_ids()
    if request.entity_id and known_entity_ids is not None and request.entity_id not in known_entity_ids:
        raise HTTPException(
            status_code=400,
            detail="Unknown customer entity_id — refresh the entity list and try again.",
        )

    try:
        result = generator.generate(
            user_query=request.query,
            reference_date=reference_date,
            dry_run=request.dry_run,
            entity_id=request.entity_id,
        )
    except Exception as e:
        logger.exception("Pipeline error")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

    if result.direct_response is not None:
        # Greeting or blocked prompt-injection attempt — never reached entity
        # resolution or SQL generation, nothing else to populate.
        return QueryResponse(
            user_query=result.user_query,
            answer=result.direct_response,
            direct_response_kind=result.direct_response_kind,
            total_time_ms=result.total_time_ms,
        )

    response = QueryResponse(
        user_query=result.user_query,
        resolved_entities=_build_resolved_entities_response(result.resolved_entities),
        extracted_sql=result.extracted_sql,
        sql_valid=result.sql_valid,
        validation_error=result.validation_error,
        clarification_needed=result.clarification_needed,
        llm_provider=result.llm_provider,
        llm_model=result.llm_model,
        total_time_ms=result.total_time_ms,
        retries=result.retries,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        error=result.error,
        suggestions=result.suggestions,
    )

    numbers_grounded = True

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

        if result.query_result.success and not request.dry_run:
            try:
                detector = AnomalyDetector()
                anom_alerts = detector.detect_anomalies(
                    query_columns=result.query_result.columns,
                    query_rows=result.query_result.rows,
                    tables_touched=result.query_result.tables_touched,
                )
                response.anomalies = [
                    AnomalyInfo(
                        transaction_id=a.transaction_id,
                        counterparty_name=a.counterparty_name,
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

        if result.query_result.success and not request.dry_run:
            try:
                answer, numbers_grounded, synth_usage = synthesize_response(
                    user_query=request.query,
                    sql=result.extracted_sql,
                    query_result=result.query_result.to_dict(),
                    llm_provider=LLM_PROVIDER,
                    llm_model=_get_llm_model(),
                    ollama_base_url=OLLAMA_BASE_URL,
                    openrouter_api_key=OPENROUTER_API_KEY,
                    openrouter_base_url=OPENROUTER_BASE_URL,
                    openai_api_key=OPENAI_API_KEY,
                    groq_key_pool=GROQ_KEY_POOL,
                    temperature=0.3,
                    max_tokens=512,
                    resolved_entities=result.resolved_entities,
                    entity_id=request.entity_id,
                )
                response.answer = answer
                response.grounding_status = synth_usage.get("grounding_status", "not_evaluated")
                response.fallback_reason = synth_usage.get("fallback_reason")
                response.prompt_tokens += synth_usage.get("prompt_tokens", 0)
                response.completion_tokens += synth_usage.get("completion_tokens", 0)
            except Exception as e:
                logger.warning("Synthesis failed: %s", e)
                response.answer = result.query_result.summary_text()
                response.grounding_status = "template"
                response.fallback_reason = "Explanation unavailable; displaying the query result summary."
        elif not result.query_result.success:
            # Valid SQL that still failed to execute even after every
            # auto-repair retry — the frontend's generic fallback text would
            # otherwise wrongly read as success (see static/app.js).
            retry_word = "attempt" if result.retries == 1 else "attempts"
            response.answer = (
                f"I generated a SQL query, but it failed to execute after {result.retries} "
                f"self-correction {retry_word}: {result.query_result.error} Try rephrasing with a more "
                f"specific counterparty name, date range, or account detail."
            )

        response.lineage_summary = _build_lineage_summary(
            tables_touched=result.query_result.tables_touched,
            resolved_entities=result.resolved_entities,
            entity_id=request.entity_id,
            row_count=result.query_result.row_count,
        )
    elif result.clarification_needed:
        response.answer = result.clarification_needed
    elif result.validation_error:
        # The LLM's output never validated as SQL at all (e.g. it answered
        # conversationally, or produced something the validator rejected) —
        # result.query_result is never set in this case either, so without
        # this branch the generic fallback text would wrongly claim success.
        response.answer = (
            f"I wasn't able to turn that into a valid SQL query: {result.validation_error} "
            f"Try rephrasing as a specific question about your transaction data — spend, "
            f"balance, or reconciliation status."
        )
    elif result.error:
        # The LLM call itself failed (network/provider error), before any
        # SQL was even produced.
        response.answer = f"Something went wrong while generating a response: {result.error}"
    elif request.dry_run:
        # dry_run returns before any execution, so result.query_result is
        # never set here — without this, the same generic fallback text
        # would wrongly claim a query ran.
        response.answer = (
            "Dry run complete — no query was executed against the database. "
            "Turn off dry-run to get a real answer."
        )

    response.numbers_grounded = numbers_grounded

    # ── Confidence assessment ──
    exec_ok = bool(result.query_result and result.query_result.success)
    r_count = result.query_result.row_count if result.query_result else 0
    resolved_list = []
    if result.resolved_entities.get("counterparty_name"):
        resolved_list.append({
            "type": "counterparty",
            "raw": result.resolved_entities["counterparty_name"],
            "canonical": result.resolved_entities["counterparty_name"],
            "similarity": result.resolved_entities.get("counterparty_match_score", 100),
        })

    conf = compute_confidence(
        sql_valid=result.sql_valid,
        execution_success=exec_ok,
        row_count=r_count,
        retries=result.retries,
        resolved_entities=resolved_list,
        clarification_needed=result.clarification_needed,
        numbers_grounded=numbers_grounded,
        grounding_status=response.grounding_status,
    )
    response.confidence = ConfidenceInfo(score=conf.score, level=conf.level, reasons=conf.reasons)

    return response


@app.get("/api/health", response_model=HealthResponse)
async def health():
    """Health check — verifies database connectivity and returns stats."""
    try:
        engine = QueryEngine()

        table_result = engine.execute("""
            SELECT COUNT(*) AS cnt FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
        """)
        table_count = table_result.rows[0][0] if table_result.success else 0

        row_result = engine.execute("""
            SELECT
                (SELECT COUNT(*) FROM bank) +
                (SELECT COUNT(*) FROM account) +
                (SELECT COUNT(*) FROM transaction) AS total
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
    """Get database schema information — tables, columns, and row counts."""
    try:
        engine = QueryEngine()
        table_info = engine.get_table_info()

        tables = []
        for table_name, columns in table_info.items():
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

        view_result = engine.execute("""
            SELECT table_name FROM information_schema.views
            WHERE table_schema = DATABASE() ORDER BY table_name
        """)
        views = [row[0] for row in view_result.rows] if view_result.success else []

        return SchemaResponse(tables=tables, views=views)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema error: {str(e)}")


@app.get("/api/history", response_model=HistoryResponse)
async def history(session_id: str = "default"):
    """Get conversation history for a session."""
    generator = _get_generator(session_id)
    turns = [
        HistoryTurn(query=turn["query"], sql=turn["sql"], summary=turn["summary"])
        for turn in generator.conversation_history
    ]
    return HistoryResponse(session_id=session_id, turns=turns)


@app.delete("/api/history")
async def clear_history(session_id: str = "default"):
    """Clear conversation history for a session."""
    if session_id in _sessions:
        _sessions[session_id].conversation_history.clear()
        return {"status": "cleared", "session_id": session_id}
    return {"status": "no_session", "session_id": session_id}


@app.post("/api/export")
async def export_csv(request: ExportRequest):
    """Execute a SQL query and return results as a downloadable CSV file."""
    from core.sql_validator import validate_sql

    validation = validate_sql(request.sql)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=f"SQL validation failed: {validation.error}")

    try:
        engine = QueryEngine()
        result = engine.execute(request.sql)

        if not result.success:
            raise HTTPException(status_code=400, detail=f"Query error: {result.error}")

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(result.columns)
        for row in result.rows:
            writer.writerow([str(v) if v is not None else "" for v in row])

        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={request.filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


@app.get("/api/counterparties")
async def list_counterparties(entity_id: str | None = None):
    """
    List distinct counterparty names extracted from transaction narration —
    useful for autocomplete. There is no vendor master table in the real
    client schema; these names come from core/description_parser.py.

    entity_id: optional — when given, scopes the list to that customer's own
    counterparties (feeds the frontend's entity-aware prompt-chip
    suggestions). Queried live against v_transaction_enriched (which carries
    entity_id) rather than v_counterparty_lookup, which is deliberately kept
    entity-agnostic since it's also the entity resolver's global fuzzy-match
    index (see db/schema.sql).
    """
    try:
        if entity_id:
            con = get_connection(readonly=True)
            try:
                with con.cursor() as cur:
                    cur.execute("""
                        SELECT counterparty_name, rail_type, COUNT(*) AS mention_count
                        FROM v_transaction_enriched
                        WHERE counterparty_name IS NOT NULL
                          AND counterparty_confidence != 'low'
                          AND entity_id = %s
                        GROUP BY counterparty_name, rail_type
                        ORDER BY mention_count DESC
                    """, [entity_id])
                    rows = cur.fetchall()
            finally:
                con.close()
            return {
                "counterparties": [
                    {"name": row[0], "rail_type": row[1], "mention_count": row[2]}
                    for row in rows
                ]
            }

        engine = QueryEngine()
        result = engine.execute("""
            SELECT counterparty_name, rail_type, mention_count
            FROM v_counterparty_lookup
            ORDER BY mention_count DESC
        """)
        if result.success:
            return {
                "counterparties": [
                    {"name": row[0], "rail_type": row[1], "mention_count": row[2]}
                    for row in result.rows
                ]
            }
        raise HTTPException(status_code=500, detail="Failed to fetch counterparties")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/entities")
async def list_entities():
    """
    List customer entity_ids with their account/bank counts — feeds the
    entity-selector dropdown. There is no login/auth in this build (out of
    scope per the problem statement), so this is how a demo user picks
    "which customer am I looking at" — a usability convenience, not an
    access-control mechanism.
    """
    try:
        engine = QueryEngine()
        result = engine.execute("""
            SELECT entity_id, account_count, bank_count, banks, bank_names
            FROM v_entity_lookup
            ORDER BY account_count DESC, entity_id
        """)
        if result.success:
            return {
                "entities": [
                    {
                        "entity_id": row[0], "account_count": row[1], "bank_count": row[2],
                        "banks": row[3], "bank_names": row[4],
                    }
                    for row in result.rows
                ]
            }
        raise HTTPException(status_code=500, detail="Failed to fetch entities")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/accuracy")
async def accuracy_summary():
    """
    Summary of the most recent `python benchmark.py` run — pass rate,
    refusal precision, PII/guardrail safety, latency, tokens/cost.
    Reads benchmark_results.json (repo root); returns available=False if
    it hasn't been generated yet.
    """
    results_path = PROJECT_ROOT / "benchmark_results.json"
    if not results_path.exists():
        return {"available": False, "message": "Run `python benchmark.py` to generate this."}

    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"available": False, "message": f"Could not read benchmark_results.json: {e}"}

    total = len(results)
    passed = sum(1 for r in results if r.get("pass"))
    by_kind = {}
    for r in results:
        kind = r.get("kind", "normal")
        by_kind.setdefault(kind, {"pass": 0, "total": 0})
        by_kind[kind]["total"] += 1
        if r.get("pass"):
            by_kind[kind]["pass"] += 1

    return {
        "available": True,
        "total_queries": total,
        "pass_rate": round(passed / total * 100, 1) if total else 0,
        "passed": passed,
        "by_kind": by_kind,
        "total_tokens_in": sum(r.get("prompt_tokens", 0) for r in results),
        "total_tokens_out": sum(r.get("completion_tokens", 0) for r in results),
        "total_cost_usd": sum(r.get("cost_usd", 0) for r in results),
    }


STATIC_DIR = PROJECT_ROOT / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return {"message": "Financial AI Chatbot API is running. Visit /docs for Swagger UI."}
