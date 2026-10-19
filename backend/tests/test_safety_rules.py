from collections import Counter

import pytest

from app.safety.handoff import HandoffRules
from app.safety.policy import check_reply, correction_notice


@pytest.mark.parametrize(
    ("said", "reason"),
    [
        ("I need to file a claim for my car.", "claim_or_complaint"),
        ("We were in an accident last week.", "claim_or_complaint"),
        ("I want to make a complaint about my last agent.", "claim_or_complaint"),
        ("Can I talk to a real person?", "asked_for_human"),
        ("Let me speak with a supervisor.", "asked_for_human"),
        ("This is ridiculous.", "caller_upset"),
        ("I'm so frustrated with this.", "caller_upset"),
    ],
)
def test_immediate_handoff_triggers(said: str, reason: str) -> None:
    assert HandoffRules().caller_said(said) == reason


@pytest.mark.parametrize(
    "said",
    [
        "I'd like to talk to an agent about switching insurers.",  # The normal booking path.
        "My email is sam@example.com.",
        "Tuesday afternoon works for me.",
    ],
)
def test_ordinary_turns_do_not_hand_off(said: str) -> None:
    assert HandoffRules().caller_said(said) is None


def test_an_out_of_scope_request_hands_off_when_it_persists() -> None:
    rules = HandoffRules()
    assert rules.caller_said("How much would my premium be?") is None  # Maya declines.
    assert rules.caller_said("Okay. My ZIP is 02139.") is None
    assert rules.caller_said("Can you just give me a quote?") == "out_of_scope"


def test_tool_rules() -> None:
    corrections = Counter({"zip_code": 2, "email": 1})
    failures = Counter({"check_availability": 2, "request_booking": 1})
    after = HandoffRules.after_tool
    assert after(
        "x", ok=True, failures=failures, corrected=["zip_code"], corrections=corrections
    ) == ("repeated_correction")
    assert (
        after("x", ok=True, failures=failures, corrected=["email"], corrections=corrections) is None
    )
    assert (
        after(
            "check_availability", ok=False, failures=failures, corrected=[], corrections=corrections
        )
        == "tool_failures"
    )
    assert (
        after("request_booking", ok=False, failures=failures, corrected=[], corrections=corrections)
        is None
    )


@pytest.mark.parametrize(
    ("reply", "rule"),
    [
        ("It would be about $120 a month.", "price"),
        ("Your premium would be around 95 dollars.", "price"),
        ("Don't worry, you'll definitely be approved.", "approval_promise"),
        ("We guarantee you'll get coverage.", "approval_promise"),
        ("You should get comprehensive coverage.", "coverage_advice"),
        ("I'd recommend a lower deductible.", "coverage_advice"),
        ("Great, you're all booked for Tuesday.", "unconfirmed_booking"),
        ("Your consultation is confirmed.", "unconfirmed_booking"),
    ],
)
def test_policy_violations(reply: str, rule: str) -> None:
    assert [v.rule for v in check_reply(reply, booking_confirmed=False)] == [rule]


@pytest.mark.parametrize(
    "reply",
    [
        "A licensed agent can recommend the right coverage for you.",
        "I've confirmed your email.",
        "Once you approve the ticket, the booking system will confirm it.",
        "I can't give prices, but an agent can.",
        "Your vehicle is a 2019 Honda.",
    ],
)
def test_compliant_replies_pass(reply: str) -> None:
    assert check_reply(reply, booking_confirmed=False) == []


def test_booking_claims_are_fine_once_a_booking_succeeded() -> None:
    assert check_reply("You're booked for Tuesday.", booking_confirmed=True) == []
    assert [v.rule for v in check_reply("That's $90. You're booked.", booking_confirmed=True)] == [
        "price"
    ]


def test_violations_name_the_sentence_and_correction() -> None:
    violations = check_reply(
        "Thanks, Sam. It's $80 a month, and you'll be approved.", booking_confirmed=False
    )
    assert [(v.rule, v.excerpt) for v in violations] == [
        ("price", "It's $80 a month, and you'll be approved."),
        ("approval_promise", "It's $80 a month, and you'll be approved."),
    ]
    notice = correction_notice(violations)
    assert notice.startswith("Compliance correction for your last reply.")
    assert "you can't give prices" in notice and "approval isn't guaranteed" in notice
