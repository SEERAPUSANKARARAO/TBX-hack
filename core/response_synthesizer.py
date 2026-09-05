"""
Response Synthesizer — Natural Language Answer Generation
=========================================================
Takes the user's query and the SQL query results, then uses the LLM to
generate a concise, conversational explanation. Unlike a prompt-only
"don't invent numbers" instruction, every number the LLM states is
verified against the actual query result before the answer is trusted —
if verification fails, the response falls back to a deterministic,
template-built answer instead of the LLM's prose. This is the
enforcement the "never hallucinate a number" requirement needs: a
check, not just an instruction.
"""
import re
import logging

import httpx

from core.llm_http import post_with_retry

logger = logging.getLogger(__name__)


def _collect_allowed_numbers(query_result: dict) -> set[str]:
    """
    Every numeric value that legitimately appears in the query result,
    normalized to a small set of comparable string forms. Any number the
    LLM states must match one of these — it's not allowed to compute or
    invent a new one (a total, a delta, a percentage, anything).
    """
    allowed = set()

    def add(value):
        try:
            f = float(value)
        except (TypeError, ValueError):
            return
        allowed.add(f"{f:.2f}")
        allowed.add(f"{f:.0f}")
        allowed.add(f"{round(f):,}")
        allowed.add(f"{f:,.2f}")

    rows = query_result.get("rows", [])
    add(len(rows))
    add(query_result.get("row_count", 0))
    for row in rows:
        values = row.values() if isinstance(row, dict) else row
        for v in values:
            add(v)

    return allowed


def _extract_numbers(text: str) -> list[str]:
    """
    Numeric tokens the LLM's answer states (currency-formatted or bare).
    The decimal group requires at least one digit after the '.' — otherwise
    a number at the end of a sentence ("...in 2026.") greedily swallows the
    full stop into the token ("2026."), which then fails an exact 4-digit
    year fullmatch downstream for no reason connected to grounding at all.
    """
    return re.findall(r'-?\d[\d,]*(?:\.\d+)?', text)


def _normalize(token: str) -> str:
    return token.replace(",", "").strip()


def _verify_numbers_grounded(answer: str, allowed_numbers: set[str]) -> bool:
    """
    True if every number-looking token in `answer` matches a value that
    actually appeared in the query result (allowing for $/comma/rounding
    formatting differences). A year (e.g. "2026") is not treated as a
    groundable figure.

    Deliberately does NOT exempt small integers in general ("top 5 results")
    — that used to be a blanket "abs(value) < 32" skip, but it let through
    exactly the most dangerous hallucination case: a small invented
    percentage or delta ("15% higher than last month") is also a small
    integer. Legitimate structural counts (row_count, len(rows)) are already
    included in `allowed_numbers` by _collect_allowed_numbers, so they don't
    need a separate exemption here — only genuinely invented figures do.
    """
    for raw in _extract_numbers(answer):
        token = _normalize(raw)
        if not token or token in (".", "-"):
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        if re.fullmatch(r'\d{4}', token) and 1900 <= value <= 2100:
            continue
        candidates = {f"{value:.2f}", f"{value:.0f}", token}
        if not candidates & allowed_numbers:
            return False
    return True


def synthesize_response(
    user_query: str,
    sql: str,
    query_result: dict,
    llm_provider: str = "openrouter",
    llm_model: str = "qwen/qwen-2.5-coder-32b-instruct",
    ollama_base_url: str = "http://localhost:11434",
    openrouter_api_key: str = "",
    openrouter_base_url: str = "https://openrouter.ai/api/v1",
    openai_api_key: str = "",
    openai_base_url: str = "https://api.openai.com/v1",
    groq_api_key: str = "",
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> tuple[str, bool, dict]:
    """
    Generate a natural language answer from SQL results.

    Returns:
        (answer_text, numbers_grounded, usage) — numbers_grounded is False
        when the LLM's prose was rejected for stating a figure not traceable
        to the query result and a deterministic template was substituted
        instead. usage is {"prompt_tokens", "completion_tokens", "cost"}.
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}

    if not query_result.get("success"):
        return f"I encountered an error running the query: {query_result.get('error', 'Unknown error')}", True, empty_usage

    if query_result.get("row_count", 0) == 0:
        return (
            "I couldn't find any matching records for that. This could mean the data doesn't "
            "exist for the filters you asked about, or a date range/counterparty name might "
            "need adjusting — I'd rather say so than guess.",
            True,
            empty_usage,
        )

    rows = query_result.get("rows", [])[:15]
    columns = query_result.get("columns", [])
    row_count = query_result.get("row_count", 0)

    result_text = _format_results_for_llm(columns, rows, row_count)

    synthesis_prompt = f"""You are a financial data analyst assistant. Based on the SQL query results below, provide a clear, concise answer to the user's question.

RULES:
1. Be conversational but precise. Use ONLY exact numbers that appear in the results below — do not
   compute, round, sum, or derive any new figure (no percentages, deltas, or trends not already
   present as a column).
2. Format currency values with commas (e.g., 12,345.67). This is Indian bank data — do not add a $ sign.
3. If there are multiple rows, summarize the key findings without inventing new numbers.
4. Keep your answer to 2-4 sentences unless more detail is genuinely needed.
5. Do NOT mention SQL or database internals. Speak as if you looked up the data directly.

USER QUESTION: {user_query}

QUERY RESULTS ({row_count} rows):
{result_text}"""

    messages = [{"role": "user", "content": synthesis_prompt}]

    try:
        response, usage = _call_llm(
            messages, llm_provider, llm_model,
            ollama_base_url, openrouter_api_key, openrouter_base_url,
            openai_api_key, openai_base_url,
            groq_api_key, temperature, max_tokens,
        )
        # The prompt instructs "no $ sign" (this is INR data), but a model
        # can ignore formatting instructions even when the numbers underneath
        # are correct — this data doesn't reach the numeric-grounding check
        # since that only extracts digits and never looks at the symbol in
        # front of them. Enforce it here instead of trusting compliance.
        answer = re.sub(r'\$(?=\d)', '', response.strip())
    except Exception as e:
        logger.warning("LLM synthesis failed, using template: %s", e)
        return _template_response(user_query, columns, rows, row_count), True, empty_usage

    allowed_numbers = _collect_allowed_numbers(query_result)
    if _verify_numbers_grounded(answer, allowed_numbers):
        return answer, True, usage

    logger.warning(
        "Synthesized answer stated a number not present in the query result — "
        "falling back to a deterministic template. answer=%r", answer
    )
    return _template_response(user_query, columns, rows, row_count), False, usage


def _format_results_for_llm(columns: list, rows: list, total_count: int) -> str:
    """Format query results as a compact text table for the LLM."""
    if not columns or not rows:
        return "No data"

    lines = [" | ".join(str(c) for c in columns), "-" * 60]
    for row in rows:
        if isinstance(row, dict):
            vals = [str(row.get(c, "")) for c in columns]
        elif isinstance(row, (list, tuple)):
            vals = [str(v) for v in row]
        else:
            vals = [str(row)]
        lines.append(" | ".join(vals))

    if total_count > len(rows):
        lines.append(f"... ({total_count - len(rows)} more rows)")

    return "\n".join(lines)


def _template_response(user_query: str, columns: list, rows: list, row_count: int) -> str:
    """Deterministic fallback: only ever restates values already in the result."""
    if row_count == 1 and len(columns) <= 3:
        row = rows[0]
        if isinstance(row, dict):
            parts = [f"{k}: {v}" for k, v in row.items()]
        else:
            parts = [f"{columns[i]}: {row[i]}" for i in range(len(columns))]
        return f"Based on the data: {', '.join(parts)}."

    if row_count <= 5:
        return f"Found {row_count} result{'s' if row_count != 1 else ''}. See the table below for details."

    return f"Found {row_count} results matching your query. The data is shown in the table below."


def _usage_from(data: dict) -> dict:
    usage = data.get("usage") or {}
    return {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "cost": usage.get("cost", 0.0),
    }


def _call_llm(
    messages: list[dict],
    provider: str,
    model: str,
    ollama_base_url: str,
    openrouter_api_key: str,
    openrouter_base_url: str,
    openai_api_key: str,
    openai_base_url: str,
    groq_api_key: str,
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict]:
    """Call the configured LLM backend for synthesis. Returns (content, usage)."""
    if provider == "openrouter":
        url = f"{openrouter_base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {openrouter_api_key}",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "FinQuery AI",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        with httpx.Client(timeout=60) as client:
            resp = post_with_retry(client, url, json=payload, headers=headers)
            data = resp.json()
            return data["choices"][0]["message"]["content"], _usage_from(data)

    elif provider == "ollama":
        url = f"{ollama_base_url}/api/chat"
        payload = {
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        with httpx.Client(timeout=60) as client:
            resp = post_with_retry(client, url, json=payload)
            data = resp.json()
            usage = {
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "cost": 0.0,
            }
            return data["message"]["content"], usage

    elif provider == "openai":
        url = f"{openai_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        with httpx.Client(timeout=60) as client:
            resp = post_with_retry(client, url, json=payload, headers=headers)
            data = resp.json()
            return data["choices"][0]["message"]["content"], _usage_from(data)

    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        with httpx.Client(timeout=60) as client:
            resp = post_with_retry(client, url, json=payload, headers=headers)
            data = resp.json()
            return data["choices"][0]["message"]["content"], _usage_from(data)

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
