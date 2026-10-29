"""Evidence packs: the agent's investigation of an alert (FR-018a, contracts/llm-draft.md).

Deterministic code gathers facts across sources, each with a stable fact_id, plain text and the
raw records behind it. Only data received and linked before the cutoff is used. The same pack is
what a drafting model is allowed to see, so every number in it is already written out in the text.
"""

import pandas as pd

from worklist.signals.events import as_of

DAY = pd.Timedelta(days=1)


def _ids(df: pd.DataFrame, limit: int = 20) -> list[str]:
    return [str(x) for x in df["raw_event_id"].head(limit)]


def build_evidence(events: pd.DataFrame, account_id, cutoff: pd.Timestamp, score: dict) -> list[dict]:
    ev = as_of(events, cutoff)
    ev = ev[ev["account_id"] == account_id].sort_values("occurred_at", ascending=False)
    age = (cutoff - ev["occurred_at"]).dt.days
    facts: list[dict] = []

    def add(kind: str, text: str, rows: pd.DataFrame | None = None) -> None:
        facts.append({"fact_id": f"f{len(facts) + 1}", "kind": kind, "text": text,
                      "raw_event_ids": _ids(rows) if rows is not None else []})

    if score.get("days_to_renewal") is not None and score.get("renewal_date") is not None:
        arr = score.get("arr")
        value = f", contract value ${arr:,.0f} per year" if arr else ""
        add("renewal", f"Renewal on {score['renewal_date']} ({int(score['days_to_renewal'])} days away){value}")

    usage = ev[ev["event_type"] == "usage.summary"]
    if len(usage) >= 2:
        latest = usage.iloc[0]
        earlier = usage[(latest["occurred_at"] - usage["occurred_at"]).dt.days.between(40, 130)]
        if len(earlier):
            before = earlier["active_users"].mean()
            days = int((latest["occurred_at"] - earlier["occurred_at"].min()).days)
            if before and abs(latest["active_users"] - before) / before >= 0.15:
                verb = "fell" if latest["active_users"] < before else "rose"
                add("usage_trend", f"Active users {verb} from {before:.0f} to {latest['active_users']:.0f} "
                                   f"over the last {days} days", pd.concat([usage.head(1), earlier]))
    seats = ev[ev["event_type"] == "seats.changed"]
    if len(usage) and len(seats) and seats.iloc[0]["seats_purchased"]:
        ratio = usage.iloc[0]["active_users"] / seats.iloc[0]["seats_purchased"]
        if ratio < 0.4:
            add("usage_level", f"{usage.iloc[0]['active_users']:.0f} of {seats.iloc[0]['seats_purchased']:.0f} "
                               f"purchased seats are active", pd.concat([usage.head(1), seats.head(1)]))

    tickets = ev[(ev["event_type"] == "ticket.created") & (age < 30)]
    if len(tickets):
        high = tickets[tickets["priority"].isin(["high", "urgent"])]
        text = f"{len(tickets)} support tickets opened in the last 30 days"
        if len(high):
            text += f", {len(high)} of them high or urgent priority"
        add("support", text, tickets)
    solved = ev[(ev["event_type"] == "ticket.solved") & (age < 90)]
    bad = solved[solved["satisfaction"] == "bad"]
    if len(bad):
        add("support_satisfaction", f"{len(bad)} tickets rated 'bad' by the customer in the last 90 days", bad)

    failed = ev[(ev["event_type"] == "invoice.payment_failed") & (age < 90)]
    if len(failed):
        add("billing", f"{len(failed)} failed payment attempts in the last 90 days "
                       f"(highest retry: attempt {int(failed['attempt'].max())})", failed)
    subs = ev[ev["event_type"] == "subscription.updated"]
    if len(subs) and subs.iloc[0]["cancel_at_period_end"] is True:
        add("billing_cancel", f"Subscription set to cancel at the end of the period "
                              f"({str(subs.iloc[0]['current_period_end'])[:10]})", subs.head(1))

    removed = ev[(ev["event_type"] == "contact.changed") & (ev["change"] == "removed") & (age < 90)]
    if len(removed):
        replaced = ev[(ev["event_type"] == "contact.changed") & (ev["change"] == "added") & (age < 90)]
        when = removed.iloc[0]["occurred_at"].date().isoformat()
        add("relationship", f"Champion contact removed in CRM on {when}"
                            + ("" if len(replaced) else " with no replacement added"), removed)
    bounced = ev[(ev["event_type"] == "email.bounced") & (age < 90)]
    if len(bounced):
        add("relationship_bounce", f"{len(bounced)} emails to the customer bounced in the last 90 days", bounced)
    at_risk = ev[(ev["event_type"] == "opportunity.updated") & (ev["stage"] == "renewal_at_risk") & (age < 90)]
    if len(at_risk):
        add("crm", "Renewal opportunity marked at risk in CRM on "
                   f"{at_risk.iloc[0]['occurred_at'].date().isoformat()}", at_risk.head(1))

    sent = ev[ev["event_type"] == "email.sent"]
    if len(sent):
        days = int((cutoff - sent.iloc[0]["occurred_at"]).days)
        add("relationship_contact", f"Last CSM email {days} days ago", sent.head(1))
    else:
        add("relationship_contact", "No CSM email on record")
    return facts
