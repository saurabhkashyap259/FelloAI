"""Draft validation (FR-023, FR-023b, contracts/llm-draft.md)."""

from worklist.agent.drafts import DraftOut, validate
from worklist.agent.templates import template_draft

PACK = {
    "account": {"name": "Acme Corp", "arr": 48000, "renewal_date": "2026-12-15"},
    "trigger": {"type": "entered_top_k"},
    "facts": [
        {"fact_id": "f1", "kind": "usage_trend", "text": "Active users fell from 42 to 17 over the last 30 days"},
        {"fact_id": "f2", "kind": "support", "text": "3 support tickets opened in the last 30 days"},
        {"fact_id": "f3", "kind": "relationship_contact", "text": "Last CSM email 47 days ago"},
    ],
    "csm_name": "Priya",
}


def _draft(**kw):
    base = {"internal_summary": "Usage fell from 42 to 17 and 3 tickets opened; call this week.",
            "email_subject": "Checking in on your team's setup",
            "email_body": "Hi Acme, I saw usage dropped from 42 to 17 users. Could we talk? Priya",
            "facts_used": ["f1", "f2"]}
    return DraftOut(**{**base, **kw})


def test_valid_draft_passes():
    assert validate(_draft(), PACK) is None


def test_unknown_or_missing_facts_fail():
    assert "unknown facts" in validate(_draft(facts_used=["f9"]), PACK)
    assert validate(_draft(facts_used=[]), PACK) == "no facts cited"


def test_invented_numbers_and_dates_fail():
    assert "numbers not in evidence" in validate(_draft(email_body="Usage fell 60% last month. Priya"), PACK)
    assert "dates not in evidence" in validate(_draft(internal_summary="Renewal 2026-11-01."), PACK)
    assert validate(_draft(internal_summary="Renewal 2026-12-15, ARR 48,000."), PACK) is None


def test_banned_words_in_customer_email_fail():
    assert "banned words" in validate(_draft(email_body="Our model flags churn risk for you. Priya"), PACK)
    assert validate(_draft(internal_summary="High risk of churn."), PACK) is None  # internal text may say it


def test_template_draft_is_valid_and_uses_only_safe_facts():
    draft = DraftOut(**template_draft(PACK))
    assert validate(draft, PACK) is None
    assert "47 days" not in draft.email_body  # internal relationship facts stay out of the customer email
