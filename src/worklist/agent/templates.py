"""Deterministic template drafts, built only from evidence facts (FR-023b fallback)."""

TRIGGER_TEXT = {
    "entered_top_k": "moved into the 'Act this week' list",
    "risk_jump": "risk rose sharply in the last week",
    "severe_event_near_renewal": "a severe event happened close to renewal",
}
# Fact kinds safe to mention to the customer (no internal CRM/billing judgements).
CUSTOMER_KINDS = ("usage_trend", "usage_level", "support", "support_satisfaction")


def template_draft(pack: dict) -> dict:
    facts = pack["facts"]
    account = pack["account"]["name"]
    summary_facts = facts[:4]
    summary = (f"{account}: {TRIGGER_TEXT.get(pack['trigger']['type'], 'material change')}. "
               + " ".join(f"{f['text']}." for f in summary_facts))
    customer = [f for f in facts if f["kind"] in CUSTOMER_KINDS][:2]
    renewal = next((f for f in facts if f["kind"] == "renewal"), None)
    lines = [f"Hi {account} team,", ""]
    if customer:
        lines.append("I wanted to check in. A couple of things caught my eye:")
        lines += [f"- {f['text']}" for f in customer]
        lines.append("")
        lines.append("Could we set up a short call this week to see what would help?")
    else:
        lines.append("I wanted to check in and see how things are going, "
                     "and whether there is anything I can help with.")
    if renewal:
        lines += ["", "It would also be a good moment to plan ahead of your renewal."]
    lines += ["", "Best,", pack["csm_name"]]
    used = [f["fact_id"] for f in summary_facts + customer] + ([renewal["fact_id"]] if renewal else [])
    return {"internal_summary": summary, "email_subject": f"Checking in: {account}",
            "email_body": "\n".join(lines), "facts_used": sorted(set(used), key=lambda x: int(x[1:]))}
