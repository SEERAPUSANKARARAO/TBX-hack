"""
Confidence Scorer — Query & Execution Confidence Index
======================================================
Computes a 0–100% confidence score for generated responses based on:
1. Entity resolution accuracy (exact vs fuzzy match)
2. SQL validation & execution pass on first try vs auto-repair retries
3. Row return completeness & query result validity
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ConfidenceAssessment:
    score: int  # 0 to 100
    level: str  # "HIGH", "MEDIUM", "LOW"
    reasons: list[str]


def compute_confidence(
    sql_valid: bool,
    execution_success: bool,
    row_count: int,
    retries: int = 0,
    resolved_entities: list[dict] = None,
    clarification_needed: Optional[str] = None,
    numbers_grounded: bool = True,
    grounding_status: str | None = None,
) -> ConfidenceAssessment:
    """Heuristic query checks, separate from explanation verification.

    A confirmed deterministic replacement does not lower query confidence.
    The score is an internal index, never a probability of answer correctness.
    """
    if clarification_needed:
        return ConfidenceAssessment(
            score=40,
            level="LOW",
            reasons=["Unresolved entity requires user clarification."],
        )

    if not sql_valid or not execution_success:
        return ConfidenceAssessment(
            score=10,
            level="LOW",
            reasons=["SQL validation or execution failed."],
        )

    if not numbers_grounded and grounding_status not in ("fallback", "template"):
        return ConfidenceAssessment(
            score=30,
            level="LOW",
            reasons=["The synthesized answer stated a figure that couldn't be verified against the "
                     "query result, so a deterministic fallback answer was used instead."],
        )

    score = 100
    reasons = ["Heuristic query checks only; not a probability of correctness."]
    if grounding_status in ("fallback", "template"):
        reasons.append("Explanation replaced with a deterministic result summary. This does not invalidate the query result.")
    elif grounding_status == "passed":
        reasons.append("Explanation numbers matched returned values; meaning and filters still require review.")
    else:
        reasons.append("Explanation verification was not performed.")

    # Retry penalty
    if retries > 0:
        score -= 15 * retries
        reasons.append(f"SQL required {retries} auto-repair iteration(s).")
    else:
        reasons.append("SQL passed validation on first attempt.")

    # Entity resolution scoring
    if resolved_entities:
        for ent in resolved_entities:
            sim = ent.get("similarity", 100)
            ent_type = ent.get("type", "entity")
            if sim < 90:
                score -= 10
                reasons.append(f"Fuzzy match on {ent_type} '{ent.get('raw')}' ({sim:.0f}% match).")
            else:
                reasons.append(f"High-confidence entity match: {ent.get('canonical')}.")

    # Row count sanity
    if row_count == 0:
        score -= 20
        reasons.append("Query returned 0 rows matching criteria.")
    else:
        reasons.append(f"Successfully retrieved {row_count} record(s).")

    score = max(0, min(100, score))

    if score >= 85:
        level = "HIGH"
    elif score >= 60:
        level = "MEDIUM"
    else:
        level = "LOW"

    return ConfidenceAssessment(score=score, level=level, reasons=reasons)
