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
) -> ConfidenceAssessment:
    """
    Calculate confidence index for the query pipeline output.
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

    score = 100
    reasons = []

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
        score = max(score - 20, 50)
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
