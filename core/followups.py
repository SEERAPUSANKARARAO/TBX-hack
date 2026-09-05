"""
Follow-up Suggestions — deterministic, entity-based
======================================================
Generated from the entities already resolved for the current answer, not
from an extra LLM call: they're free (no added tokens/cost/latency) and
guaranteed answerable, since they reuse a counterparty/bank/date that's
already known to exist in the data.
"""

GENERIC_SUGGESTIONS = [
    "Who are our top 5 counterparties by spend?",
    "Show unreconciled transactions over 50000",
    "Break down credits and debits by bank this year",
]


def build_followup_suggestions(resolved_entities: dict, query_succeeded: bool) -> list[str]:
    """Build up to 3 follow-up question suggestions for the just-answered query."""
    if not query_succeeded:
        return []

    suggestions: list[str] = []
    counterparty = resolved_entities.get("counterparty_name")
    bank_name = resolved_entities.get("bank_name")
    has_dates = bool(resolved_entities.get("start_date"))

    if counterparty:
        name = counterparty.title()
        if has_dates:
            suggestions.append("How does that compare to the previous period?")
        else:
            suggestions.append("And what about last month?")
        suggestions.append(f"Show unreconciled transactions for {name}")
        suggestions.append(f"What's the largest single payment to {name}?")
    elif bank_name:
        name = bank_name.title()
        suggestions.append(f"What's the balance breakdown across accounts at {name}?")
        suggestions.append(f"Break down credits and debits at {name} this year")
    elif has_dates:
        suggestions.append("How does that compare to the previous period?")

    for s in GENERIC_SUGGESTIONS:
        if len(suggestions) >= 3:
            break
        if s not in suggestions:
            suggestions.append(s)

    return suggestions[:3]
