"""Outreach drafts for new alerts (US4, FR-023, contracts/llm-draft.md).

Claude drafts from the evidence pack only; a validator checks every cited fact, number and date
against the pack; anything that fails (or drafting off, no key, timeout, API error) falls back to the
deterministic template, labelled with the reason. The system never sends email (FR-024).
"""

import hashlib
import json
import re

import anthropic
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Connection, text

from worklist.agent.templates import template_draft
from worklist.config import get_settings
from worklist.db import tables as t

PROMPT_VERSION = "draft-v1"
BANNED = ("risk", "score", "churn", "model", "probability")
SYSTEM = """You draft outreach for a Customer Success Manager (CSM) about one customer account.
You receive an evidence pack of facts, each with a fact_id. Write:
- internal_summary: 2-4 sentences for the CSM explaining what changed and what to do first.
- email_subject (max 80 characters) and email_body (max 1200 characters): an email the CSM could send to
  the customer, written as the CSM, signed with the CSM's name. Offer concrete help grounded in the
  facts (for example a check-in on adoption after a usage drop, or on open support issues).
- facts_used: the fact_ids your text relies on.
Rules: use only the facts provided. Do not invent numbers, dates, names or events; any number or date
you write must appear in the evidence pack. The email must not mention risk, scores, models, churn,
probabilities or internal monitoring. Be helpful, specific and short."""


class DraftOut(BaseModel):
    internal_summary: str
    email_subject: str = Field(max_length=80)
    email_body: str = Field(max_length=1200)
    facts_used: list[str]


def evidence_pack(conn: Connection, alert: dict) -> dict:
    """Only the fields FR-023a allows: account name, contract value, renewal date, plain-language facts,
    CSM first name, and what changed. No contact names, emails, raw payloads or ticket bodies."""
    account = conn.execute(text("SELECT a.name, a.arr, a.renewal_date, u.display_name FROM account a "
                                "LEFT JOIN app_user u ON u.id = a.owner_user_id WHERE a.id = :a"),
                           {"a": alert["account_id"]}).mappings().first()
    first = (account["display_name"] or "").split(" ")[0] or "The Customer Success team"
    return {
        "account": {"name": account["name"], "arr": round(account["arr"] or 0),
                    "renewal_date": account["renewal_date"].isoformat() if account["renewal_date"] else None},
        "trigger": {"type": alert["trigger"]},
        "facts": [{"fact_id": f["fact_id"], "kind": f["kind"], "text": f["text"]} for f in alert["evidence"]],
        "csm_name": first,
    }


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _numbers(s: str) -> set[str]:
    out = set()
    for token in NUMBER.findall(DATE.sub(" ", s)):
        value = token.replace(",", "").rstrip(".")
        out.add(value[:-2] if value.endswith(".0") else value)
    return out


def validate(draft: DraftOut, pack: dict) -> str | None:
    """Returns None if valid, else the failure detail (FR-023b)."""
    ids = {f["fact_id"] for f in pack["facts"]}
    if not draft.facts_used:
        return "no facts cited"
    unknown = set(draft.facts_used) - ids
    if unknown:
        return f"unknown facts {sorted(unknown)}"
    pack_text = json.dumps(pack)
    allowed_numbers, allowed_dates = _numbers(pack_text), set(DATE.findall(pack_text))
    for field in (draft.internal_summary, draft.email_subject, draft.email_body):
        extra_dates = set(DATE.findall(field)) - allowed_dates
        if extra_dates:
            return f"dates not in evidence {sorted(extra_dates)}"
        extra = _numbers(field) - allowed_numbers
        if extra:
            return f"numbers not in evidence {sorted(extra)}"
    customer_text = (draft.email_subject + " " + draft.email_body).lower()
    banned = [w for w in BANNED if re.search(rf"\b{w}", customer_text)]
    if banned:
        return f"banned words {banned}"
    return None


def call_claude(pack: dict) -> DraftOut:
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s, max_retries=0)
    response = client.beta.messages.parse(
        model=settings.llm_model,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Evidence pack:\n" + json.dumps(pack, indent=2)}],
        output_format=DraftOut,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        raise RefusalError()
    if response.parsed_output is None:
        raise ValidationError.from_exception_data("DraftOut", [])
    return response.parsed_output


class RefusalError(Exception):
    pass


def generate(pack: dict, enabled: bool) -> tuple[dict, str, str | None]:
    """Returns (draft fields, generator, fallback_reason)."""
    settings = get_settings()
    reason = None
    if not enabled:
        reason = "disabled"
    elif not settings.anthropic_api_key:
        reason = "no_api_key"
    else:
        try:
            draft = call_claude(pack)
            problem = validate(draft, pack)
            if problem is None:
                return draft.model_dump(), "llm", None
            reason = f"validation_failed:{problem}"
        except anthropic.APITimeoutError:
            reason = "timeout"
        except RefusalError:
            reason = "api_error:refusal"
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            reason = f"api_error:{type(exc).__name__}"
        except ValidationError:
            reason = "validation_failed:schema"
    return template_draft(pack), "template", reason


def draft_for_new_alerts(conn: Connection, run_id, settings: dict, use_llm: bool) -> dict:
    alerts = conn.execute(text(
        "SELECT al.* FROM alert al WHERE al.run_id = :r AND NOT EXISTS "
        "(SELECT 1 FROM outreach_draft d WHERE d.alert_id = al.id)"), {"r": run_id}).mappings().all()
    created, fallbacks = 0, 0
    for alert in alerts:
        pack = evidence_pack(conn, dict(alert))
        fields, generator, reason = generate(pack, bool(settings["llm_drafting_enabled"]) and use_llm)
        evidence_hash = hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()
        conn.execute(text(
            "INSERT INTO outreach_draft (id, tenant_id, alert_id, generator, fallback_reason, llm_model, "
            "prompt_version, evidence_hash, internal_summary, email_subject, email_body, facts_used, status) "
            "VALUES (:id, :t, :a, :g, :fr, :m, :pv, :h, :s, :subj, :body, :facts, 'draft') "
            "ON CONFLICT (alert_id) DO NOTHING"),
            {"id": t.new_id(), "t": alert["tenant_id"], "a": alert["id"], "g": generator, "fr": reason,
             "m": get_settings().llm_model if generator == "llm" else None, "pv": PROMPT_VERSION, "h": evidence_hash,
             "s": fields["internal_summary"], "subj": fields["email_subject"], "body": fields["email_body"],
             "facts": fields["facts_used"]})
        created += 1
        fallbacks += generator == "template"
    return {"drafts_created": created, "drafts_template": fallbacks}
