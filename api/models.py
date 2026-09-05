"""
Pydantic Models — Request/Response Schemas
==========================================
Typed data models for the FastAPI endpoints.
"""
from pydantic import BaseModel, Field
from typing import Any


# ── Request Models ──

class QueryRequest(BaseModel):
    """Request body for the /api/query endpoint."""
    query: str = Field(
        ...,
        description="Natural language question about financial data",
        min_length=1,
        max_length=2000,
        examples=["How much did we spend on AWS last quarter?"],
    )
    session_id: str = Field(
        default="default",
        description="Session ID for multi-turn conversation history",
    )
    dry_run: bool = Field(
        default=False,
        description="If true, skip LLM call and return assembled prompt only",
    )
    entity_id: str | None = Field(
        default=None,
        description="Selected customer entity_id (from the /api/entities dropdown — there is no "
                    "login in this build). When set, every query is scoped to this entity's accounts.",
    )


class ExportRequest(BaseModel):
    """Request body for the /api/export endpoint."""
    sql: str = Field(
        ...,
        description="SQL query to execute and export results as CSV",
    )
    filename: str = Field(
        default="export.csv",
        description="Filename for the CSV download",
    )


# ── Response Models ──

class QueryResultData(BaseModel):
    """Structured query result data."""
    success: bool
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    row_count: int = 0
    execution_time_ms: float = 0.0
    sql: str = ""
    error: str | None = None
    tables_touched: list[str] = []


class AnomalyInfo(BaseModel):
    """Statistical anomaly alert."""
    transaction_id: str | None = None
    counterparty_name: str
    amount: float
    historical_mean: float
    historical_std: float
    threshold: float
    percentage_above_mean: float
    message: str


class ConfidenceInfo(BaseModel):
    """Confidence scoring information."""
    score: int
    level: str  # HIGH, MEDIUM, LOW
    reasons: list[str] = []


class QueryResponse(BaseModel):
    """Response body for the /api/query endpoint."""
    user_query: str
    answer: str = ""
    resolved_entities: dict = {}
    extracted_sql: str = ""
    sql_valid: bool = False
    validation_error: str | None = None
    query_result: QueryResultData | None = None
    clarification_needed: str | None = None
    anomalies: list[AnomalyInfo] = []
    confidence: ConfidenceInfo | None = None
    grounding_status: str = "not_evaluated"
    numbers_grounded: bool = True
    llm_provider: str = ""
    llm_model: str = ""
    total_time_ms: float = 0.0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None
    direct_response_kind: str | None = None  # "greeting" | "blocked" — set when no SQL was generated at all
    suggestions: list[str] = []
    lineage_summary: str | None = None  # plain-English "what data this answer came from" (see api/main.py)


class HealthResponse(BaseModel):
    """Response for the /api/health endpoint."""
    status: str = "ok"
    database: str = "connected"
    tables: int = 0
    total_rows: int = 0
    llm_provider: str = ""
    llm_model: str = ""


class SchemaInfo(BaseModel):
    """Table schema information."""
    table_name: str
    columns: list[dict[str, str]]
    row_count: int = 0


class SchemaResponse(BaseModel):
    """Response for the /api/schema endpoint."""
    tables: list[SchemaInfo] = []
    views: list[str] = []


class HistoryTurn(BaseModel):
    """A single conversation turn."""
    query: str
    sql: str
    summary: str


class HistoryResponse(BaseModel):
    """Response for the /api/history endpoint."""
    session_id: str
    turns: list[HistoryTurn] = []
