"""
SQL Generator — Full Pipeline Orchestrator
============================================
Orchestrates the end-to-end Text-to-SQL pipeline:
Entity Resolution → Prompt Assembly → LLM Call → SQL Extraction
→ Validation → Execution → (Auto-Repair if needed)

Supports multiple LLM backends: Ollama (local), OpenAI, Groq.
"""
import re
import json
import time
import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from core.prompts import build_system_prompt, build_user_message, build_repair_prompt
from core.few_shot_examples import format_few_shot_messages
from core.sql_validator import extract_sql_from_response, validate_sql, sanitize_sql
from core.query_engine import QueryEngine, QueryResult
from core.entity_resolver import EntityResolver, ResolvedEntities

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Full result from the Text-to-SQL pipeline."""
    # User input
    user_query: str = ""
    resolved_entities: dict = field(default_factory=dict)
    prompt: str = ""

    # LLM output
    raw_llm_response: str = ""
    extracted_sql: str = ""
    sql_valid: bool = False
    validation_error: str | None = None

    # Execution
    query_result: QueryResult | None = None

    # Metadata
    llm_provider: str = ""
    llm_model: str = ""
    total_time_ms: float = 0.0
    retries: int = 0

    # Error state
    error: str | None = None
    clarification_needed: str | None = None  # If vendor not found, suggest alternatives

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
            "error": self.error,
            "clarification_needed": self.clarification_needed,
        }


class SQLGenerator:
    """
    Orchestrates the full Text-to-SQL pipeline.

    Usage:
        generator = SQLGenerator(
            db_path="db/financial.duckdb",
            llm_provider="ollama",
            llm_model="qwen2.5-coder:7b-instruct"
        )
        result = generator.generate("How much did we spend on AWS?")
        print(result.query_result.summary_text())
    """

    def __init__(
        self,
        db_path: str,
        llm_provider: str = "ollama",
        llm_model: str = "qwen2.5-coder:7b-instruct",
        ollama_base_url: str = "http://localhost:11434",
        openrouter_api_key: str = "",
        openrouter_base_url: str = "https://openrouter.ai/api/v1",
        openai_api_key: str = "",
        openai_base_url: str = "https://api.openai.com/v1",
        groq_api_key: str = "",
        max_retries: int = 1,
        fuzzy_threshold: int = 90,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self.db_path = db_path
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

        # Initialize components
        self.query_engine = QueryEngine(db_path)
        self.entity_resolver = EntityResolver(db_path, fuzzy_threshold)

        # Conversation history for multi-turn
        self.conversation_history: list[dict] = []

    def generate(
        self,
        user_query: str,
        reference_date: date | None = None,
        dry_run: bool = False,
    ) -> PipelineResult:
        """
        Run the full Text-to-SQL pipeline.

        Args:
            user_query: Natural language question about financial data.
            reference_date: Reference date for relative date expressions.
            dry_run: If True, skip the LLM call and return the assembled prompt.

        Returns:
            PipelineResult with SQL, execution results, and metadata.
        """
        start_time = time.perf_counter()
        result = PipelineResult(user_query=user_query)

        # ── Step 1: Entity Resolution ──
        entities = self.entity_resolver.resolve(user_query, reference_date)
        result.resolved_entities = entities.to_dict()

        # Check for unresolved vendor — need clarification
        if entities.unresolved_vendor:
            # Try to suggest similar vendors
            suggestions = self._suggest_vendors(entities.unresolved_vendor)
            if suggestions:
                result.clarification_needed = (
                    f"I couldn't find vendor '{entities.unresolved_vendor}' in the database. "
                    f"Did you mean: {', '.join(suggestions)}?"
                )
            else:
                result.clarification_needed = (
                    f"I couldn't find vendor '{entities.unresolved_vendor}' in the database. "
                    f"Please check the vendor name and try again."
                )
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 2: Build Messages ──
        system_prompt = build_system_prompt(
            resolved_entities=entities.to_dict(),
            current_date=str(reference_date or date.today()),
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
            # Show the assembled prompt
            logger.info("=== SYSTEM PROMPT ===\n%s", system_prompt)
            logger.info("=== USER MESSAGE ===\n%s", user_message)
            return result

        # ── Step 3: Call LLM ──
        try:
            llm_response = self._call_llm(messages)
            result.raw_llm_response = llm_response
            result.llm_provider = self.llm_provider
            result.llm_model = self.llm_model
        except Exception as e:
            result.error = f"LLM call failed: {str(e)}"
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 4: Extract & Validate SQL ──
        sql = extract_sql_from_response(llm_response)
        sql = sanitize_sql(sql)
        result.extracted_sql = sql

        validation = validate_sql(sql)
        result.sql_valid = validation.is_valid

        # ── Step 5: Auto-repair loop ──
        retries = 0
        while not validation.is_valid and retries < self.max_retries:
            retries += 1
            result.retries = retries
            logger.warning(
                "SQL validation failed (attempt %d): %s",
                retries, validation.error
            )

            # Build repair prompt
            repair_msg = build_repair_prompt(sql, validation.error)
            repair_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": repair_msg},
            ]

            try:
                repair_response = self._call_llm(repair_messages)
                sql = extract_sql_from_response(repair_response)
                sql = sanitize_sql(sql)
                result.extracted_sql = sql
                result.raw_llm_response += f"\n\n--- REPAIR ATTEMPT {retries} ---\n{repair_response}"

                validation = validate_sql(sql)
                result.sql_valid = validation.is_valid
            except Exception as e:
                result.validation_error = f"Repair attempt {retries} failed: {str(e)}"
                break

        if not validation.is_valid:
            result.validation_error = validation.error
            result.total_time_ms = (time.perf_counter() - start_time) * 1000
            return result

        # ── Step 6: Execute SQL ──
        query_result = self.query_engine.execute(sql)
        result.query_result = query_result

        # ── Step 7: Store in conversation history ──
        if query_result.success:
            summary = (
                f"Returned {query_result.row_count} rows. "
                f"Tables: {', '.join(query_result.tables_touched)}"
            )
            if query_result.rows and query_result.columns:
                # Add first row as a sample
                first_row = dict(zip(query_result.columns, query_result.rows[0]))
                summary += f" Sample: {first_row}"

            self.conversation_history.append({
                "query": user_query,
                "sql": sql,
                "summary": summary,
            })
            # Keep only last 5 turns
            if len(self.conversation_history) > 5:
                self.conversation_history = self.conversation_history[-5:]

        result.total_time_ms = (time.perf_counter() - start_time) * 1000
        return result

    def _suggest_vendors(self, mention: str) -> list[str]:
        """Suggest similar vendor names for a failed match."""
        from rapidfuzz import fuzz, process

        all_names = []
        for v in self.entity_resolver.vendors:
            all_names.append(v["vendor_name"])
            if v["vendor_alias"]:
                all_names.append(f"{v['vendor_name']} ({v['vendor_alias']})")

        results = process.extract(
            mention,
            [v["vendor_name"] for v in self.entity_resolver.vendors],
            scorer=fuzz.token_sort_ratio,
            limit=3,
        )
        return [r[0] for r in results if r[1] > 50]

    def _call_llm(self, messages: list[dict]) -> str:
        """
        Call the configured LLM backend.

        Args:
            messages: List of message dicts with 'role' and 'content'.

        Returns:
            The LLM's response text.
        """
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

    def _call_openrouter(self, messages: list[dict]) -> str:
        """Call OpenRouter API (OpenAI-compatible with attribution headers)."""
        url = f"{self.openrouter_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "FinQuery AI",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _call_ollama(self, messages: list[dict]) -> str:
        """Call Ollama local API."""
        url = f"{self.ollama_base_url}/api/chat"
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        with httpx.Client(timeout=120) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["message"]["content"]

    def _call_openai(self, messages: list[dict]) -> str:
        """Call OpenAI-compatible API."""
        url = f"{self.openai_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _call_groq(self, messages: list[dict]) -> str:
        """Call Groq API (OpenAI-compatible)."""
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]
