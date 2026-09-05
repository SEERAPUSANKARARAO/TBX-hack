"""
Input Classifier — pre-pipeline guardrail
============================================
Runs before entity resolution / SQL generation, with zero LLM calls.
Catches two cases that shouldn't reach the Text-to-SQL pipeline at all:

1. Greetings / chit-chat ("hi", "thanks") — answered directly instead of
   burning an LLM call trying to write SQL for "hi".
2. Prompt-injection / role-manipulation attempts — blocked with a fixed
   message before they ever reach the model.

This is deliberately NOT a general content-moderation system — a
hardcoded profanity list is easy to bypass and prone to false positives,
and isn't the actual risk surface for a text-to-SQL assistant. The real
threat here is someone trying to get the SQL-generation model to ignore
its role or narrate PII it was never allowed to see; that's a precise,
well-defined pattern this CAN catch reliably.
"""
import re
from dataclasses import dataclass

GREETING_PATTERNS = [
    (r'^\s*(hi|hello|hey|hiya|yo)(\s+there)?\s*[!.]*\s*$', "greeting"),
    (r'^\s*good\s*(morning|afternoon|evening)\s*[!.]*\s*$', "greeting"),
    (r'^\s*(thanks|thank\s*you|thx|ty)\s*[!.]*\s*$', "thanks"),
    (r'^\s*(bye|goodbye|see\s*you|later)\s*[!.]*\s*$', "farewell"),
    (r'^\s*(how\s*are\s*you\??|what\'?s\s*up\??|sup)\s*$', "greeting"),
    (r'^\s*(ok|okay|cool|great|nice|got\s*it|sounds\s*good)\s*[!.]*\s*$', "acknowledgement"),
    # Compound greeting + pleasantry ("hi, how are you", "hello, what's up?")
    # — the plain single-phrase patterns above require the WHOLE message be
    # just the greeting OR just the pleasantry, so a natural combination of
    # both fell through to the SQL pipeline entirely (a real reported bug).
    (r'^\s*(hi|hello|hey|hiya|yo)\s*[,!.]*\s*(how\s*are\s*you\??|what\'?s\s*up\??|sup)\s*[!.?]*\s*$', "greeting"),
    (r'^\s*good\s*(morning|afternoon|evening)\s*[,!.]*\s*how\s*are\s*you\??\s*[!.?]*\s*$', "greeting"),
]

GREETING_RESPONSES = {
    "greeting": "Hi! Ask me anything about your bank transactions — spend, balances, or reconciliation status.",
    "thanks": "You're welcome! Let me know if you have another question.",
    "farewell": "Goodbye! Come back anytime you have a finance question.",
    "acknowledgement": "Got it — anything else you'd like to check?",
}

# Known prompt-injection / jailbreak patterns. Precise and narrow by
# design: false positives here block a legitimate finance question,
# so this only matches well-established manipulation phrasing.
INJECTION_PATTERNS = [
    r'ignore\s+(all\s+)?(previous|prior|above)\s+instructions',
    r'disregard\s+(all\s+)?(previous|prior|above)\s+instructions',
    r'reveal\s+your\s+(system\s+)?prompt',
    r'what\s+(is|are)\s+your\s+(system\s+)?(instructions|prompt)',
    r'you\s+are\s+now\s+(a|an)\s',
    r'act\s+as\s+(if\s+you\s+are|a)\s',
    r'pretend\s+(you\s+are|to\s+be)\s',
    r'\bjailbreak\b',
    r'\bDAN\s+mode\b',
    r'without\s+any\s+(restrictions|filters|guardrails)',
]

INJECTION_RESPONSE = (
    "I can only answer questions about your bank transaction data — I can't change my "
    "instructions, reveal internal configuration, or bypass how account numbers and UTRs "
    "are masked."
)


@dataclass
class InputClassification:
    kind: str  # "data_question" | "greeting" | "blocked"
    response: str | None = None


def classify_input(query: str) -> InputClassification:
    """Classify a raw user query before it reaches entity resolution / SQL generation."""
    stripped = query.strip()
    lowered = stripped.lower()

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return InputClassification(kind="blocked", response=INJECTION_RESPONSE)

    if len(stripped) <= 40:
        for pattern, label in GREETING_PATTERNS:
            if re.search(pattern, lowered):
                return InputClassification(kind="greeting", response=GREETING_RESPONSES[label])

    return InputClassification(kind="data_question")
