"""When a call goes to a licensed human, decided in code rather than by the model.

Triggers: the caller has a claim or complaint, asks for a human, or is upset; an
out-of-scope request persists; the same detail is corrected twice; a tool fails twice;
verification fails; or a booking is incomplete. The model can also ask (handoff_to_human).
"""

import re
import uuid
from collections import Counter
from typing import Literal

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog, Call, Handoff

HandoffReason = Literal[
    "caller_upset",
    "asked_for_human",
    "claim_or_complaint",
    "out_of_scope",
    "repeated_correction",
    "tool_failures",
    "verification_failed",
    "booking_incomplete",
    "model_request",
]

REASON_LABELS: dict[HandoffReason, str] = {
    "caller_upset": "Caller is upset",
    "asked_for_human": "Caller asked for a person",
    "claim_or_complaint": "Claim or complaint",
    "out_of_scope": "Repeated out-of-scope request",
    "repeated_correction": "Same detail corrected twice",
    "tool_failures": "A step failed twice",
    "verification_failed": "Verification failed",
    "booking_incomplete": "Booking incomplete",
    "model_request": "Maya asked for help",
}


def reason_label(reason: str) -> str:
    return next((label for key, label in REASON_LABELS.items() if key == reason), reason)


_CLAIM_OR_COMPLAINT = re.compile(
    r"\b(?:file|filing|make|making|open|start|report)\s+(?:a\s+)?claim\b|\bclaim number\b"
    r"|\b(?:i|we)\s+(?:was|were|got|had been)\s+in\s+an?\s+(?:accident|crash|collision)\b"
    r"|\bcomplain(?:t|ts|ing)?\b",
    re.IGNORECASE,
)
# "An agent" is the normal booking path, so only explicit asks for a person count.
_ASKED_FOR_HUMAN = re.compile(
    r"\b(?:speak|talk)\s+(?:to|with)\s+(?:a|an|the|your)?\s*(?:real\s+|live\s+|actual\s+)?"
    r"(?:human|person|representative|manager|supervisor)\b"
    r"|\b(?:real|live|actual)\s+(?:person|human)\b|\bnot\s+a\s+(?:robot|bot|machine)\b",
    re.IGNORECASE,
)
_UPSET = re.compile(
    r"\b(?:angry|furious|pissed|frustrated|fed up|sick of|ridiculous|unacceptable|useless"
    r"|terrible|horrible|awful|hate (?:this|you)|waste of (?:my )?time|stupid"
    r"|wtf|damn|crap|shit\w*|fuck\w*)\b",
    re.IGNORECASE,
)
_OUT_OF_SCOPE = re.compile(
    r"\b(?:how much|price|pricing|quote|premium|cost|cheap(?:er|est)?)\b"
    r"|\b(?:should i (?:get|buy|choose|pick|add)|which (?:coverage|plan|policy)"
    r"|what coverage|recommend)\b"
    r"|\b(?:lawyer|attorney|sue|lawsuit|legal advice|medical advice|financial advice"
    r"|tax advice)\b",
    re.IGNORECASE,
)


class HandoffRules:
    """Per-call handoff triggers. Pure logic: no I/O."""

    def __init__(self) -> None:
        self._out_of_scope = 0

    def caller_said(self, text: str) -> HandoffReason | None:
        if _CLAIM_OR_COMPLAINT.search(text):
            return "claim_or_complaint"
        if _ASKED_FOR_HUMAN.search(text):
            return "asked_for_human"
        if _UPSET.search(text):
            return "caller_upset"
        if _OUT_OF_SCOPE.search(text):
            self._out_of_scope += 1  # Maya declines the first time; a repeat persists.
            if self._out_of_scope >= 2:
                return "out_of_scope"
        return None

    @staticmethod
    def after_tool(
        name: str,
        *,
        ok: bool,
        failures: Counter[str],
        corrected: list[str],
        corrections: Counter[str],
    ) -> HandoffReason | None:
        if any(corrections[field] >= 2 for field in corrected):
            return "repeated_correction"
        if not ok and failures[name] >= 2:
            return "tool_failures"
        return None


class HandoffService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def open(self, call_id: uuid.UUID, reason: HandoffReason) -> bool:
        """Open a handoff and flag the call. False if one is already open for this call."""
        async with self._sessionmaker() as db, db.begin():
            handoff_id = await db.scalar(
                pg_insert(Handoff)
                .values(id=uuid.uuid4(), call_id=call_id, reason=reason, status="open")
                .on_conflict_do_nothing(
                    index_elements=[Handoff.call_id], index_where=text("status = 'open'")
                )
                .returning(Handoff.id)
            )
            if handoff_id is None:
                return False
            await db.execute(update(Call).where(Call.id == call_id).values(flagged=True))
            db.add(
                AuditLog(
                    call_id=call_id,
                    actor="system",
                    event="handoff.created",
                    payload={"handoff_id": str(handoff_id), "reason": reason},
                )
            )
        return True
