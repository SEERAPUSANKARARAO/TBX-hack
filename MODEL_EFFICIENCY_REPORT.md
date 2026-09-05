# Model Efficiency & Accuracy Report

_Generated 2026-09-05T11:57:06 by `benchmark.py`. All numbers below are measured from real API calls against the configured model — none are estimated or hardcoded.

## Model choice

- **Provider:** `openrouter`
- **Model:** `z-ai/glm-5.2:free`
- **Rationale:** the pipeline never asks the LLM to do arithmetic — it only asks it to (1) translate a question into SQL against a small, pre-aggregated schema, and (2) narrate an already-computed result in plain language. Both tasks are within reach of a small/cheap model; the accuracy and grounding guarantees come from the deterministic layers around the model (SQL validation, PII column blocking, execution-time auto-repair, and post-hoc numeric verification against the query result), not from model size. This is the efficiency argument: correctness is enforced by code, so the model only needs to be good enough at structured generation, not large.

## Headline numbers

| Metric | Value |
|---|---|
| Total queries | 36 |
| Pass rate | 19/36 (52.8%) |
| Refusal precision (unanswerable questions) | 100% |
| PII guardrail safety rate | 100% |
| Destructive-SQL guardrail block rate | 100% |
| Avg latency | 20339 ms |
| P50 / P95 latency | 28476 ms / 31518 ms |
| Total tokens (in / out) | 31851 / 1604 |
| Total cost (measured) | $0.000000 |
| Avg cost / query | $0.000000 |
| Synthesis step included in cost? | No — SQL generation only; run with --with-synthesis for full end-to-end cost |

## By category

| Category | Pass | Total | Avg latency (ms) |
|---|---|---|---|
| Spend Aggregation | 3 | 5 | 21056 |
| Date Filters | 3 | 5 | 21118 |
| Balances | 1 | 3 | 23831 |
| Reconciliation | 0 | 3 | 29940 |
| Credit/Debit | 0 | 2 | 30059 |
| Fuzzy NLU | 2 | 4 | 27698 |
| Multi-Turn | 1 | 4 | 27861 |
| PII Guardrail | 2 | 2 | 15459 |
| Guardrails | 2 | 2 | 1 |
| Refusal | 4 | 4 | 1 |
| Analytical Views | 1 | 2 | 23377 |

## What's enforced by code, not by prompting

- **Numeric grounding**: every number in a synthesized answer is checked against the actual query result before being shown; a mismatch falls back to a deterministic template instead of the LLM's prose (see `core/response_synthesizer.py`).
- **PII masking**: `account_number` / `utr_number` cannot be selected raw — blocked at SQL validation time, and masked again defensively at the query-execution layer even if that were ever bypassed (see `core/sql_validator.py`, `core/query_engine.py`).
- **Read-only execution**: query execution always connects as a dedicated MySQL user with SELECT-only grants (`finquery_ro`) — a real database-level guarantee, independent of the SQL-statement-type guardrail (see `core/db_connection.py`).
- **Execution-time auto-repair**: a syntactically valid query that fails at execution (e.g. wrong column) is fed back to the model with the real MySQL error and retried, not just pre-execution validation failures.
- **Pre-aggregated views**: counterparty spend questions are steered toward `v_counterparty_spend_summary` rather than ad hoc joins over raw tables, to avoid join fan-out silently inflating a sum.

## Operational notes

- Some queries during this run took several seconds due to upstream rate limiting on the configured OpenRouter model/tier (`post_with_retry` in `core/llm_http.py` backs off and retries automatically). If this model's tier has a tight shared rate limit, consider a paid tier or the already-wired Groq fallback (`LLM_PROVIDER=groq`) for a live demo, so back-to-back judge questions don't stack up 429 retries.
- This report reflects the currently configured model only. To compare against a different model (a frontier model as a baseline, or a different small model), change `OPENROUTER_MODEL` (or `LLM_PROVIDER`) in `.env` and re-run `python benchmark.py` — this script does not fabricate comparison numbers for models it hasn't actually called.
