"""
SQL Generator — Full Pipeline Orchestrator
============================================
Orchestrates the end-to-end Text-to-SQL pipeline:
Entity Resolution -> Prompt Assembly -> LLM Call -> SQL Extraction
-> Validation -> Execution -> (Auto-Repair on validation OR execution failure)

Supports multiple LLM backends: OpenRouter, Ollama (local), OpenAI, Groq.
"""
import time
import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from core.prompts import build_system_prompt, build_user_message, build_repair_prompt
from core.few_shot_examples import format_few_shot_messages
from core.sql_validator import extract_sql_from_response, validate_sql, sanitize_sql, enforce_row_limit, ValidationResult
from core.query_engine import QueryEngine, QueryResult
from core.entity_resolver import EntityResolver
from core.data_bounds import get_date_range
from core.llm_http import post_with_retry
from core.input_classifier import classify_input
from core.followups import build_followup_suggestions

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Full result from the Text-to-SQL pipeline."""
    user_query: str = ""
    resolved_entities: dict = field(default_factory=dict)
    prompt: str = ""

    raw_llm_response: str = ""
    extracted_sql: str = ""
    sql_valid: bool = False
    validation_error: str | None = None

    query_result: QueryResult | None = None

    llm_provider: str = ""
    llm_model: str = ""
    total_time_ms: float = 0.0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    error: str | None = None
    clarification_needed: str | None = None

    # Set when core.input_classifier short-circuits before the pipeline runs.
    direct_response: str | None = None
    direct_response_kind: str | None = None  # "greeting" | "blocked"

    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "user_query": self.user_query,
            "resolved_entities": self.resolved_entities,
            "extracted_sql": self.extracted_sql,
            "sql_valid": self.sql_valid,
            "validation_error": self.validation_error,
            "query_result": self.query_result.to_dict() if self.query_result else None,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "total_time_ms": round(self.total_time_ms, 2),
            "retries": self.retries,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "error": self.error,
            "clarification_needed": self.clarification_needed,
            "direct_response": self.direct_response,
            "direct_response_kind": self.direct_response_kind,
            "suggestions": self.suggestions,
        }


class SQLGenerator:
    """
    Orchestrates the full Text-to-SQL pipeline. Database connection details
    live in config.py / core.db_connection — nothing here is DB-specific.

    Usage:
        generator = SQLGenerator(llm_provider="openrouter", ...)
        result = generator.generate("How much did we send to Amazon last month?")
    """

    def __init__(
        self,
        llm_provider: str = "openrouter",
        llm_model: str = "qwen/qwen-2.5-coder-32b-instruct",
        ollama_base_url: str = "http://localhost:11434",
        openrouter_api_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        openai_api_key: str = "",
        openai_base_url: str = "https://api.openai.com/v1",
        groq_api_key: str = "",
        max_retries: int = 2,
        fuzzy_threshold: int = 90,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.openrouter_api_key = openrouter_api_key
        self.openrouter_base_url = openrouter_base_url.rstrip("/")
        self.openai_api_key = openai_api_key
        self.openai_base_url = openai_base_url.rstrip("/")
        self.groq_api_key = groq_api_key
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.query_engine = QueryEngine()
        self.entity_resolver = EntityResolver(fuzzy_threshold)

        self.conversation_history: list[dict] = []
        self.last_usage: dict = {}

    def generate(
        self,
        user_query: str,
        reference_date: date | None = None,
        dry_run: bool = False,
        entity_id: str | None = None,
    ) -> PipelineResult:
        """
        Run the full Text-to-SQL pipeline.

        Args:
            user_query: Natural language question about bank transaction data.
            reference_date: Reference date for relative date expressions —
                should be the data's own max date (core.data_bounds), not
                necessarily wall-clock today.
            dry_run: If True, skip the LLM call and return the assembled prompt.
            entity_id: The customer selected in the UI's entity dropdown (there
                is no login in this build — see README). When set, generated
                SQL is required to filter to this entity_id; this is a
                usability scope, not a security boundary (the DB user can
                still read every entity's rows — see core/db_connection.py).

        Returns:
            PipelineResult with SQL, execution results, and metadata.
        """
        start_time = time.perf_counter()
        result = PipelineResult(user_query=user_query)

        def _validate(candidate_sql: str):
            v = validate_sql(candidate_sql)
            if v.is_valid and entity_id and entity_id not in candidate_sql:
                return ValidationResult(
                    is_valid=False, sql=candidate_sql,
                    error=(
                        f"BLOCKED: a customer is selected (entity_id='{entity_id}') but the query doesn't "
                        f"filter by it. Every account/transaction reference must be scoped to this entity_id "
                        f"— join to account and add `account.entity_id = '{entity_id}'` (or filter directly "
                        f"on the enriched views' entity_id column)."
                    ),
                )
            return v

        # ── Step 0: Pre-pipeline classification (no LLM call) ──
        # Greetings and prompt-injection attempts never reach entity
        # resolution or SQL generation at all.
        classification = classify_input(user_query)
        if classification.kind in ("greeting", "blocked"):
            result.direct_response = classification.response
            result.direct_response_kind = classification.kind
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 1: Entity Resolution ──
        entities = self.entity_resolver.resolve(user_query, reference_date)
        result.resolved_entities = entities.to_dict()

        if entities.unresolved_counterparty:
            suggestions = self._suggest_counterparties(entities.unresolved_counterparty)
            if suggestions:
                result.clarification_needed = (
                    f"I couldn't find '{entities.unresolved_counterparty}' in the transaction data. "
                    f"Did you mean: {', '.join(suggestions)}?"
                )
            else:
                result.clarification_needed = (
                    f"I couldn't find '{entities.unresolved_counterparty}' in the transaction data. "
                    f"Please check the name and try again."
                )
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 2: Build Messages ──
        min_date, max_date = get_date_range()
        date_range_str = f"{min_date} to {max_date}" if min_date and max_date else "unknown"

        system_prompt = build_system_prompt(
            resolved_entities=entities.to_dict(),
            current_date=str(reference_date) if reference_date else (str(max_date) if max_date else None),
            date_range=date_range_str,
            entity_id=entity_id,
        )
        user_message = build_user_message(user_query, self.conversation_history)
        few_shot = format_few_shot_messages()

        messages = [
            {"role": "system", "content": system_prompt},
            *few_shot,
            {"role": "user", "content": user_message},
        ]
        result.prompt = system_prompt + "\n\n" + user_message

        if dry_run:
            result.raw_llm_response = "[DRY RUN — LLM not called]"
            result.extracted_sql = "[DRY RUN]"
            result.llm_provider = "dry_run"
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            logger.info("=== SYSTEM PROMPT ===\n%s", system_prompt)
            logger.info("=== USER MESSAGE ===\n%s", user_message)
            return result

        # ── Step 3: Call LLM ──
        try:
            llm_response = self._call_llm(messages)
            result.raw_llm_response = llm_response
            result.llm_provider = self.llm_provider
            result.llm_model = self.llm_model
            result.prompt_tokens += self.last_usage.get("prompt_tokens", 0)
            result.completion_tokens += self.last_usage.get("completion_tokens", 0)
            result.cost_usd += self.last_usage.get("cost", 0.0)
        except Exception as e:
            result.error = f"LLM call failed: {str(e)}"
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 4: Extract & Validate SQL ──
        sql = sanitize_sql(extract_sql_from_response(llm_response))
        result.extracted_sql = sql
        validation = _validate(sql)
        result.sql_valid = validation.is_valid

        # ── Step 5: Unified auto-repair loop — covers BOTH pre-execution
        # validation failures AND execution-time failures (e.g. valid SQL
        # syntax that references a column that doesn't exist), which are
        # both real Text-to-SQL failure modes and both deserve a retry. ──
        retries = 0
        query_result: QueryResult | None = None

        while retries <= self.max_retries:
            if not validation.is_valid:
                error_for_repair = validation.error
            else:
                exec_sql = enforce_row_limit(sql)
                query_result = self.query_engine.execute(exec_sql)
                if query_result.success:
                    break
                error_for_repair = query_result.error

            if retries >= self.max_retries:
                break

            retries += 1
            result.retries = retries
            logger.warning("SQL failed (attempt %d): %s", retries, error_for_repair)

            repair_msg = build_repair_prompt(sql, error_for_repair)
            repair_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": repair_msg},
            ]
            try:
                repair_response = self._call_llm(repair_messages)
                result.prompt_tokens += self.last_usage.get("prompt_tokens", 0)
                result.completion_tokens += self.last_usage.get("completion_tokens", 0)
                result.cost_usd += self.last_usage.get("cost", 0.0)
                sql = sanitize_sql(extract_sql_from_response(repair_response))
                result.extracted_sql = sql
                result.raw_llm_response += f"\n\n--- REPAIR ATTEMPT {retries} ---\n{repair_response}"
                validation = _validate(sql)
                result.sql_valid = validation.is_valid
            except Exception as e:
                result.validation_error = f"Repair attempt {retries} failed: {str(e)}"
                break

        if not validation.is_valid:
            result.validation_error = validation.error
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # Whenever validation.is_valid is True here, the loop above already
        # executed in that same pass (the `else` branch always runs execute()
        # immediately), so query_result is guaranteed set — never None.
        result.query_result = query_result

        # ── Step 6: Store in conversation history ──
        if query_result.success:
            summary = f"Returned {query_result.row_count} rows."
            if query_result.rows and query_result.columns:
                first_row = dict(zip(query_result.columns, query_result.rows[0]))
                summary += f" Sample: {first_row}"

            self.conversation_history.append({"query": user_query, "sql": sql, "summary": summary})
            if len(self.conversation_history) > 5:
                self.conversation_history = self.conversation_history[-5:]

        result.suggestions = build_followup_suggestions(result.resolved_entities, query_result.success)

        result.total_time_ms = (time.perf_counter() - start_time) * 1000
        return result

    def _suggest_counterparties(self, mention: str) -> list[str]:
        """Suggest similar counterparty names for a failed match."""
        from rapidfuzz import fuzz, process

        names = [c["counterparty_name"] for c in self.entity_resolver.counterparties]
        results = process.extract(mention, names, scorer=fuzz.token_sort_ratio, limit=3)
        return [r[0] for r in results if r[1] > 50]

    def _call_llm(self, messages: list[dict]) -> str:
        """Call the configured LLM backend. Populates self.last_usage."""
        if self.llm_provider == "openrouter":
            return self._call_openrouter(messages)
        elif self.llm_provider == "ollama":
            return self._call_ollama(messages)
        elif self.llm_provider == "openai":
            return self._call_openai(messages)
        elif self.llm_provider == "groq":
            return self._call_groq(messages)
        else:
            raise ValueError(f"Unknown LLM provider: {self.llm_provider}")

    def _record_openai_style_usage(self, data: dict) -> None:
        usage = data.get("usage") or {}
        self.last_usage = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost": usage.get("cost", 0.0),  # OpenRouter reports actual $ cost per call; other providers: 0
        }

    def _call_openrouter(self, messages: list[dict]) -> str:
        url = f"{self.openrouter_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "FinQuery AI",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model, "messages": messages,
            "temperature": self.temperature, "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = post_with_retry(client, url, json=payload, headers=headers)
            data = response.json()
            self._record_openai_style_usage(data)
            return data["choices"][0]["message"]["content"]

    def _call_ollama(self, messages: list[dict]) -> str:
        url = f"{self.ollama_base_url}/api/chat"
        payload = {
            "model": self.llm_model, "messages": messages, "stream": False,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        with httpx.Client(timeout=120) as client:
            response = post_with_retry(client, url, json=payload)
            data = response.json()
            self.last_usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            }
            return data["message"]["content"]

    def _call_openai(self, messages: list[dict]) -> str:
        url = f"{self.openai_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.openai_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.llm_model, "messages": messages,
            "temperature": self.temperature, "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = post_with_retry(client, url, json=payload, headers=headers)
            data = response.json()
            self._record_openai_style_usage(data)
            return data["choices"][0]["message"]["content"]

    def _call_groq(self, messages: list[dict]) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.groq_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.llm_model, "messages": messages,
            "temperature": self.temperature, "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = post_with_retry(client, url, json=payload, headers=headers)
            data = response.json()
            self._record_openai_style_usage(data)
            return data["choices"][0]["message"]["content"]
