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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import httpx

from core.llm_http import post_with_retry, post_with_key_rotation
from core.api_key_pool import ApiKeyPool

logger = logging.getLogger(__name__)


class NumericEvidence(set):
    """Exact values grouped by unit so counts/amounts cannot justify percentages."""
    def __init__(self):
        super().__init__()
        self.percentages = set()
        self.non_percentages = set()


def _collect_allowed_numbers(query_result: dict) -> set[str]:
    """Keep exact decimal values; do not widen acceptance by integer rounding."""
    allowed = NumericEvidence()
    def add(value, column=""):
        try:
            number = Decimal(str(value))
            if number.is_finite():
                allowed.add(str(number))
                target = allowed.percentages if re.search(r"percent|pct", column, re.I) else allowed.non_percentages
                target.add(str(number))
        except (InvalidOperation, ValueError, TypeError):
            pass
    add(query_result.get("row_count", 0))
    rows = query_result.get("rows", [])
    add(len(rows))
    for row in rows:
        pairs = row.items() if isinstance(row, dict) else zip(query_result.get("columns", []), row)
        for column, value in pairs:
            if re.search(r"(^|_)(id|number|reference|utr)(_|$)", column, re.I):
                continue
            add(value, column)
    return allowed


_NUMBER = r"[+−-]?\d[\d,]*(?:\.\d+)?"

def _extract_numbers(text: str) -> list[str]:
    return re.findall(_NUMBER, text)


def _normalize(token: str) -> str:
    return token.replace(",", "").replace("−", "-").strip()


def _verify_numbers_grounded(answer: str, allowed_numbers: set[str]) -> bool:
    """Numeric consistency only, not semantic accuracy. Fail closed on ambiguity.

    Decimal rounding is checked at the displayed precision (at least two
    decimal places for non-integers). Explicit decrease/increase wording
    directly before an unsigned magnitude supplies its direction.
    """
    if not answer.strip() or re.fullmatch(r"(?:processing|loading|thinking)[.\s…]*", answer.strip(), re.I):
        return False
    allowed = [Decimal(_normalize(n)) for n in allowed_numbers]
    for match in re.finditer(_NUMBER, answer):
        token = _normalize(match.group())
        value = Decimal(token)
        prefix = answer[max(0, match.start()-60):match.start()]
        direction = re.search(r"\b(decreased?|declined?|fell|dropped?|reduced?|increased?|rose|grew)\s+by\s*(?:[₹$]\s*)?$", prefix, re.I)
        if direction:
            # Signed magnitudes after 'by' are ambiguous: use the safe template.
            if token.startswith(("-", "+")):
                return False
            negative = direction.group(1).lower().startswith(('decreas', 'declin', 'fell', 'drop', 'reduc'))
            value = -value if negative else value
        # A year is context only with explicit temporal wording, never a blanket exemption.
        if (not direction and re.fullmatch(r"\d{4}", token) and 1900 <= value <= 2100
                and re.search(r"(?:\bin|\bfor|January|February|March|April|May|June|July|August|September|October|November|December)\s+$", prefix, re.I)):
            continue
        candidates = allowed
        if isinstance(allowed_numbers, NumericEvidence):
            suffix = answer[match.end():]
            is_percent = bool(re.match(r"\s*(?:%|percent\b|percentage points\b)", suffix, re.I))
            pool = allowed_numbers.percentages if is_percent else allowed_numbers.non_percentages
            candidates = [Decimal(n) for n in pool]
        places = len(token.split('.')[1]) if '.' in token else 0
        quantum = Decimal(1).scaleb(-max(2, places))
        if not any(value == source or (places >= 2 and value == source.quantize(quantum, rounding=ROUND_HALF_UP)) for source in candidates):
            return False
    return True


def _describe_filters(resolved_entities: dict | None, entity_id: str | None) -> str:
    """Short human-readable description of what was searched for, built from
    whichever of counterparty/bank/dates were actually resolved — used to
    make the "no data" message name what came up empty instead of a fully
    generic apology."""
    resolved_entities = resolved_entities or {}
    parts = []
    if resolved_entities.get("counterparty_name"):
        parts.append(f"counterparty '{resolved_entities['counterparty_name']}'")
    if resolved_entities.get("bank_name"):
        parts.append(f"bank '{resolved_entities['bank_name']}'")
    if resolved_entities.get("start_date") and resolved_entities.get("end_date"):
        parts.append(f"between {resolved_entities['start_date']} and {resolved_entities['end_date']}")
    if entity_id:
        parts.append("for the selected customer")
    return " ".join(parts)


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
    groq_key_pool: ApiKeyPool | None = None,
    temperature: float = 0.3,
    max_tokens: int = 512,
    resolved_entities: dict | None = None,
    entity_id: str | None = None,
) -> tuple[str, bool, dict]:
    """
    Generate a natural language answer from SQL results.

    Args:
        resolved_entities, entity_id: optional context (counterparty/bank/
            dates resolved for this query, and the selected customer, if
            any) used only to make the 0-row "no data" message name what
            was actually searched for — not used when rows are returned.

    Returns:
        (answer_text, numbers_grounded, usage) — numbers_grounded is False
        when the LLM's prose was rejected for stating a figure not traceable
        to the query result and a deterministic template was substituted
        instead. usage is {"prompt_tokens", "completion_tokens", "cost"}.
    """
    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "grounding_status": "not_evaluated"}

    if not query_result.get("success"):
        return f"I encountered an error running the query: {query_result.get('error', 'Unknown error')}", True, empty_usage

    if query_result.get("row_count", 0) == 0:
        filters_desc = _describe_filters(resolved_entities, entity_id)
        if filters_desc:
            message = (
                f"I couldn't find any transactions matching {filters_desc}. This could mean the data "
                f"doesn't exist for that combination, or a name/date might need adjusting — I'd rather "
                f"say so than guess."
            )
        else:
            message = (
                "I couldn't find any matching records for that. This could mean the data doesn't "
                "exist for the filters you asked about, or a date range/counterparty name might "
                "need adjusting — I'd rather say so than guess."
            )
        return message, True, empty_usage

    rows = query_result.get("rows", [])[:15]
    columns = query_result.get("columns", [])
    row_count = query_result.get("row_count", 0)

    result_text = _format_results_for_llm(columns, rows, row_count)

    synthesis_prompt = f"""You are a financial data analyst assistant. Based on the SQL query results below, provide a clear, concise answer to the user's question.

RULES:
1. Be conversational but precise. Use ONLY exact numbers that appear in the results below — do not
   compute, sum, or derive any new figure (no percentages, deltas, or trends not already
   present as a column).
2. You may round existing decimal values to two decimal places. Preserve signs.
   Format currency values with commas (e.g., 12,345.67). This is Indian bank data — do not add a $ sign.
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
            groq_key_pool, temperature, max_tokens,
        )
        # The prompt instructs "no $ sign" (this is INR data), but a model
        # can ignore formatting instructions even when the numbers underneath
        # are correct — this data doesn't reach the numeric-grounding check
        # since that only extracts digits and never looks at the symbol in
        # front of them. Enforce it here instead of trusting compliance.
        answer = re.sub(r'\$(?=\d)', '', response.strip())
    except Exception as e:
        logger.warning("LLM synthesis failed, using template: %s", e)
        return _template_response(user_query, columns, rows, row_count), True, {**empty_usage, "grounding_status": "template", "fallback_reason": "Explanation service unavailable; displaying values directly from the result."}

    allowed_numbers = _collect_allowed_numbers(query_result)
    if _verify_numbers_grounded(answer, allowed_numbers):
        return answer, True, {**usage, "grounding_status": "passed"}

    logger.warning("Explanation numeric verification failed; using result template")
    return _template_response(user_query, columns, rows, row_count), False, {
        **usage, "grounding_status": "fallback",
        "fallback_reason": "Generated explanation did not pass numeric verification; displaying values directly from the result."
    }



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
    if row_count == 1 and rows:
        row = rows[0]
        if isinstance(row, dict):
            parts = [f"{k.replace('_', ' ')}: {v}" for k, v in row.items()]
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
    groq_key_pool: ApiKeyPool | None,
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
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        build_headers = lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=60) as client:
            resp = post_with_key_rotation(client, url, json=payload, key_pool=groq_key_pool or ApiKeyPool([], name="groq"),
                                           build_headers=build_headers)
            data = resp.json()
            return data["choices"][0]["message"]["content"], _usage_from(data)

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
