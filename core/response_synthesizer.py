"""
Response Synthesizer — Natural Language Answer Generation
=========================================================
Takes the user's query and the SQL query results, then uses
the LLM to generate a concise, conversational explanation.
Falls back to a template-based response if the LLM is unavailable.
"""
import logging

import httpx

logger = logging.getLogger(__name__)


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
) -> str:
    """
    Generate a natural language answer from SQL results.

    Args:
        user_query: The original user question.
        sql: The executed SQL query.
        query_result: Dict with 'columns', 'rows', 'row_count', etc.
        llm_provider: Which LLM backend to use.
        llm_model: Model name.
        (other LLM connection params)

    Returns:
        A conversational answer string.
    """
    # If no results or error, return a template response
    if not query_result.get("success"):
        return f"I encountered an error running the query: {query_result.get('error', 'Unknown error')}"

    if query_result.get("row_count", 0) == 0:
        return (
            "The query returned no results. This could mean the data doesn't "
            "exist for the specified filters, or the date range/vendor name "
            "might need adjustment."
        )

    # Build the synthesis prompt
    # Limit rows to avoid token overflow
    rows = query_result.get("rows", [])[:15]
    columns = query_result.get("columns", [])
    row_count = query_result.get("row_count", 0)

    # Format results as a compact table
    result_text = _format_results_for_llm(columns, rows, row_count)

    synthesis_prompt = f"""You are a financial data analyst assistant. Based on the SQL query results below, provide a clear, concise answer to the user's question.

RULES:
1. Be conversational but precise. Use exact numbers from the results.
2. Format currency values with $ and commas (e.g., $12,345.67).
3. If there are multiple rows, summarize the key findings.
4. Keep your answer to 2-4 sentences unless more detail is needed.
5. Do NOT mention SQL or database internals. Speak as if you looked up the data directly.
6. If results show trends or notable patterns, briefly mention them.

USER QUESTION: {user_query}

QUERY RESULTS ({row_count} rows):
{result_text}"""

    messages = [
        {"role": "user", "content": synthesis_prompt}
    ]

    try:
        response = _call_llm(
            messages, llm_provider, llm_model,
            ollama_base_url, openrouter_api_key, openrouter_base_url,
            openai_api_key, openai_base_url,
            groq_api_key, temperature, max_tokens,
        )
        return response.strip()
    except Exception as e:
        logger.warning("LLM synthesis failed, using template: %s", e)
        return _template_response(user_query, columns, rows, row_count)


def _format_results_for_llm(columns: list, rows: list, total_count: int) -> str:
    """Format query results as a compact text table for the LLM."""
    if not columns or not rows:
        return "No data"

    lines = []
    # Header
    lines.append(" | ".join(str(c) for c in columns))
    lines.append("-" * 60)

    # Data rows
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


def _template_response(
    user_query: str,
    columns: list,
    rows: list,
    row_count: int,
) -> str:
    """
    Generate a basic template-based response when LLM is unavailable.
    Tries to be useful by extracting key values from the results.
    """
    if row_count == 1 and len(columns) <= 3:
        # Single-value result (e.g., SUM, COUNT)
        row = rows[0]
        if isinstance(row, dict):
            parts = [f"{k}: {v}" for k, v in row.items()]
        else:
            parts = [f"{columns[i]}: {row[i]}" for i in range(len(columns))]
        return f"Based on the data: {', '.join(parts)}."

    if row_count <= 5:
        return f"Found {row_count} result{'s' if row_count != 1 else ''}. See the table below for details."

    return f"Found {row_count} results matching your query. The data is shown in the table below."


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
) -> str:
    """Call the configured LLM backend for synthesis."""
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
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    elif provider == "ollama":
        url = f"{ollama_base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()["message"]["content"]

    elif provider == "openai":
        url = f"{openai_base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    elif provider == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {groq_api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        with httpx.Client(timeout=60) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")
