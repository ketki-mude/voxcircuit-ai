"""Deterministic checks on every finalized assistant transcript.

Speech cannot be stopped before it plays, so this detects violations after the fact: the
handler logs a policy event, flags the call, and tells the model to correct itself.
"""

import re
from dataclasses import dataclass
from typing import Literal

PolicyRule = Literal["price", "approval_promise", "coverage_advice", "unconfirmed_booking"]


@dataclass(frozen=True)
class Violation:
    rule: PolicyRule
    excerpt: str  # The sentence that broke the rule.


_RULES: dict[PolicyRule, re.Pattern[str]] = {
    "price": re.compile(
        r"\$\s?\d|\b\d[\d,]*(?:\.\d+)?\s?(?:dollars|bucks|usd)\b"
        r"|\b(?:premium|price|cost|rate)s?\s+(?:is|are|would be|will be|of|around|about)"
        r"\s+(?:about\s+|around\s+|roughly\s+)?\$?\d",
        re.IGNORECASE,
    ),
    "approval_promise": re.compile(
        r"\byou(?:'ll| will)\s+(?:definitely\s+|certainly\s+|surely\s+)?(?:be|get)\s+approved\b"
        r"|\byou(?:'re| are)\s+(?:pre-?)?approved\b"
        r"|\bguarantee(?:s|d)?\b.{0,40}\b(?:approv\w*|coverage|accept\w*)\b",
        re.IGNORECASE,
    ),
    "coverage_advice": re.compile(
        r"\b(?:you should|you'll want|you need|i(?:'d| would)? recommend|i suggest|go with"
        r"|the best)\b.{0,60}\b(?:coverage|liability|collision|comprehensive|deductible"
        r"|policy|plan)\b",
        re.IGNORECASE,
    ),
    "unconfirmed_booking": re.compile(
        r"\byou(?:'re| are)\s+(?:all\s+)?(?:booked|scheduled|confirmed|set for)\b"
        r"|\byou've been (?:booked|scheduled)\b"
        r"|\bi(?:'ve| have)\s+(?:booked|scheduled)\s+(?:you|your|the|a)\b"
        r"|\byour (?:appointment|consultation|call|meeting)\s+(?:is|has been)\s+"
        r"(?:booked|scheduled|confirmed|set)\b",
        re.IGNORECASE,
    ),
}

# Pointing the caller to a licensed agent about coverage is the right answer, not advice.
_REFERRAL = re.compile(r"\b(?:licensed\s+)?agent\b", re.IGNORECASE)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")

CORRECTIONS: dict[PolicyRule, str] = {
    "price": "You stated a price or cost, but you have no pricing data. Tell the caller "
    "you misspoke, you can't give prices, and a licensed agent can discuss pricing.",
    "approval_promise": "You suggested the caller will be approved. Tell the caller "
    "approval isn't guaranteed and a licensed agent will review it.",
    "coverage_advice": "You gave coverage advice. Tell the caller you can't recommend "
    "coverage and a licensed agent can help them choose.",
    "unconfirmed_booking": "You said the consultation is booked, but no booking has been "
    "confirmed. Tell the caller it is not booked yet.",
}


def check_reply(text: str, *, booking_confirmed: bool) -> list[Violation]:
    """Rule violations in one assistant reply, at most one per rule."""
    found: dict[PolicyRule, Violation] = {}
    for sentence in _SENTENCES.split(text.strip()):
        for rule, pattern in _RULES.items():
            if rule in found:
                continue
            if rule == "unconfirmed_booking" and booking_confirmed:
                continue
            if rule == "coverage_advice" and _REFERRAL.search(sentence):
                continue
            if pattern.search(sentence):
                found[rule] = Violation(rule, sentence[:200])
    return list(found.values())


def correction_notice(violations: list[Violation]) -> str:
    fixes = " ".join(CORRECTIONS[v.rule] for v in violations)
    return f"Compliance correction for your last reply. {fixes} Do this now, briefly."
