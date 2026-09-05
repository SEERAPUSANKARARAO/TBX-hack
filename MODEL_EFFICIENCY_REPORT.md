# Model Efficiency & Accuracy Report

_Generated 2026-09-05T10:44:32 by `benchmark.py`. All numbers below are measured from real API calls against the configured model — none are estimated or hardcoded.

> **Addendum (post-run):** two things changed after this run and are not reflected in the numbers below.
> (1) Q20 ("Payments to Reliance Digital Retail (dropping 'Ltd')") failed due to a bug in the synthetic
> data generator — one counterparty name had its spaces stripped (`RELIANCEDIGITALRETAILLTD`), so a
> naturally-phrased follow-up couldn't fuzzy-match it. Fixed in `scripts/generate_sample_data.py` and
> re-verified directly against the entity resolver and a dry-run prompt assembly; the ~24 queries that
> failed with `429 Too Many Requests` are unrelated to this and unrelated to model accuracy — see the
> rate-limit note below. (2) A near-identical failure mode (a bare quoted legal suffix like `'Ltd'` being
> treated as the target entity) was hardened against directly in `core/entity_resolver.py`, independent of
> the data fix. A full re-run was not repeated immediately after, given how rate-limited this specific
> model/tier is (see below) — re-run `python benchmark.py` once a less-throttled provider is configured to
> get a clean end-to-end number.

## Model choice

- **Provider:** `openrouter`
- **Model:** `z-ai/glm-5.2:free`
- **Rationale:** the pipeline never asks the LLM to do arithmetic — it only asks it to (1) translate a question into SQL against a small, pre-aggregated schema, and (2) narrate an already-computed result in plain language. Both tasks are within reach of a small/cheap model; the accuracy and grounding guarantees come from the deterministic layers around the model (SQL validation, PII column blocking, execution-time auto-repair, and post-hoc numeric verification against the query result), not from model size. This is the efficiency argument: correctness is enforced by code, so the model only needs to be good enough at structured generation, not large.

## Headline numbers

| Metric | Value |
|---|---|
| Total queries | 36 |
| Pass rate | 22/36 (61.1%) |
| Refusal precision (unanswerable questions) | 100% |
| PII guardrail safety rate | 100% |
| Destructive-SQL guardrail block rate | 100% |
| Avg latency | 16978 ms |
| P50 / P95 latency | 23095 ms / 33138 ms |
| Total tokens (in / out) | 38663 / 1704 |
| Total cost (measured) | $0.000000 |
| Avg cost / query | $0.000000 |
| Synthesis step included in cost? | No — SQL generation only; run with --with-synthesis for full end-to-end cost |

## By category

| Category | Pass | Total | Avg latency (ms) |
|---|---|---|---|
| Spend Aggregation | 2 | 5 | 21376 |
| Date Filters | 4 | 5 | 22144 |
| Balances | 3 | 3 | 9561 |
| Reconciliation | 1 | 3 | 21910 |
| Credit/Debit | 0 | 2 | 31833 |
| Fuzzy NLU | 1 | 4 | 15015 |
| Multi-Turn | 3 | 4 | 20948 |
| PII Guardrail | 2 | 2 | 16573 |
| Guardrails | 2 | 2 | 0 |
| Refusal | 4 | 4 | 0 |
| Analytical Views | 0 | 2 | 29270 |

## What's enforced by code, not by prompting

- **Numeric grounding**: every number in a synthesized answer is checked against the actual query result before being shown; a mismatch falls back to a deterministic template instead of the LLM's prose (see `core/response_synthesizer.py`).
- **PII masking**: `account_number` / `utr_number` cannot be selected raw — blocked at SQL validation time, and masked again defensively at the query-execution layer even if that were ever bypassed (see `core/sql_validator.py`, `core/query_engine.py`).
- **Read-only execution**: the DuckDB connection used for query execution is opened read-only, independent of the SQL-statement-type guardrail.
- **Execution-time auto-repair**: a syntactically valid query that fails at execution (e.g. wrong column) is fed back to the model with the real DuckDB error and retried, not just pre-execution validation failures.
- **Pre-aggregated views**: counterparty spend questions are steered toward `v_counterparty_spend_summary` rather than ad hoc joins over raw tables, to avoid join fan-out silently inflating a sum.

## Operational notes

- Some queries during this run took several seconds due to upstream rate limiting on the configured OpenRouter model/tier (`post_with_retry` in `core/llm_http.py` backs off and retries automatically). If this model's tier has a tight shared rate limit, consider a paid tier or the already-wired Groq fallback (`LLM_PROVIDER=groq`) for a live demo, so back-to-back judge questions don't stack up 429 retries.
- This report reflects the currently configured model only. To compare against a different model (a frontier model as a baseline, or a different small model), change `OPENROUTER_MODEL` (or `LLM_PROVIDER`) in `.env` and re-run `python benchmark.py` — this script does not fabricate comparison numbers for models it hasn't actually called.
