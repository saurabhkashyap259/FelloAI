"""Account facts resolved by source precedence, with conflicts and account status (FR-005).

Billing wins for contract value and renewal date, CRM for owner and segment. Facts are computed
as of a cutoff from the point-in-time event frame, so training, scoring and display agree.
"""

from datetime import timedelta
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import Connection, text

from worklist.db import tables as t

RENEWAL_TOLERANCE_DAYS = 7
ARR_TOLERANCE = 0.05
GRACE = {"term": 90, "monthly": 30}


def _latest(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values("occurred_at").groupby("account_id").tail(1).set_index("account_id")


def resolve_facts(events: pd.DataFrame, accounts: pd.DataFrame, users_by_email: dict,
                  cutoff: pd.Timestamp) -> pd.DataFrame:
    out = pd.DataFrame(index=accounts.index)
    out["contract_type"] = accounts["contract_type"]

    crm = _latest(events[(events["event_type"] == "account.upserted")])
    owners = events[(events["event_type"] == "account.upserted") & events["owner_email"].notna()]
    owner_latest = _latest(owners)
    seg = _latest(events[(events["event_type"] == "account.upserted") & events["segment"].notna()])
    crm_arr = _latest(events[(events["event_type"] == "account.upserted") & events["arr"].notna()])
    subs = events[events["event_type"] == "subscription.updated"]
    sub_latest = _latest(subs)
    paying = _latest(subs[subs["mrr"] > 0])
    invoices = _latest(events[events["event_type"] == "invoice.paid"])

    out["owner_email"] = owner_latest["owner_email"].reindex(out.index)
    out["owner_user_id"] = out["owner_email"].map(lambda e: users_by_email.get(str(e).lower()) if
                                                  isinstance(e, str) else None)
    out["segment"] = seg["segment"].reindex(out.index).astype(object).fillna(accounts["master_segment"])
    out["crm_renewal"] = pd.to_datetime(crm["renewal_date"].reindex(out.index), errors="coerce")
    out["crm_arr"] = crm_arr["arr"].reindex(out.index)
    out["billing_arr"] = (paying["mrr"] * 12).reindex(out.index)
    billing_renewal = pd.to_datetime(sub_latest["current_period_end"].reindex(out.index), errors="coerce")
    # Monthly plans: Stripe only sends updates on change; the period rolls with each paid invoice.
    invoice_next = (invoices["occurred_at"].dt.tz_localize(None).dt.normalize()
                    + pd.Timedelta(days=30)).reindex(out.index)
    monthly = out["contract_type"] == "monthly"
    cancelled = sub_latest["cancel_at_period_end"].reindex(out.index).fillna(False).astype(bool)
    rolled = billing_renewal.where(~monthly | cancelled, np.fmax(billing_renewal, invoice_next))
    out["billing_renewal"] = rolled
    out["cancel_at_period_end"] = cancelled
    out["renewal_date"] = out["billing_renewal"].fillna(out["crm_renewal"])
    out["arr"] = out["billing_arr"].fillna(out["crm_arr"])
    out["has_facts"] = out["renewal_date"].notna()

    # Status: overdue during grace after a passed renewal, churned after grace (spec Definitions),
    # outcome unknown when CRM says it renewed but billing shows no new period.
    today = cutoff.tz_localize(None).normalize() if cutoff.tzinfo else cutoff.normalize()
    grace = out["contract_type"].map(GRACE).fillna(90)
    passed = out["renewal_date"] < today
    won = events[(events["event_type"] == "opportunity.updated") & (events["stage"] == "closed_won")]
    won_latest = _latest(won)
    won_after = (pd.to_datetime(won_latest["occurred_at"].reindex(out.index)).dt.tz_localize(None)
                 >= out["renewal_date"])
    status = pd.Series("active", index=out.index)
    status[passed] = "renewal_overdue"
    status[passed & won_after.fillna(False)] = "outcome_unknown"
    status[passed & (today > out["renewal_date"] + pd.to_timedelta(grace, unit="D"))] = "churned"
    status[~out["has_facts"]] = "not_started"
    out["status"] = status
    out["days_to_renewal"] = (out["renewal_date"] - today).dt.days
    return out


def conflicts_for(facts: pd.DataFrame) -> list[dict]:
    """Conflicts beyond tolerance between CRM and billing (renewal ±7 days, ARR ±5%)."""
    found = []
    for account_id, row in facts.iterrows():
        # Monthly periods roll in billing only; CRM doesn't track them, so only term dates are compared.
        if row["contract_type"] == "term" and pd.notna(row["crm_renewal"]) and pd.notna(row["billing_renewal"]):
            if abs((row["crm_renewal"] - row["billing_renewal"]).days) > RENEWAL_TOLERANCE_DAYS:
                found.append({"account_id": account_id, "fact": "renewal_date", "chosen_source": "billing",
                              "chosen_value": row["billing_renewal"].date().isoformat(), "other_source": "crm",
                              "other_value": row["crm_renewal"].date().isoformat()})
        if pd.notna(row["crm_arr"]) and pd.notna(row["billing_arr"]) and row["billing_arr"] > 0:
            if abs(row["crm_arr"] - row["billing_arr"]) / row["billing_arr"] > ARR_TOLERANCE:
                found.append({"account_id": account_id, "fact": "arr", "chosen_source": "billing",
                              "chosen_value": f"{row['billing_arr']:.0f}", "other_source": "crm",
                              "other_value": f"{row['crm_arr']:.0f}"})
    return found


def apply_facts(conn: Connection, facts: pd.DataFrame, run_id: UUID | None, tenant_id: UUID) -> int:
    """Writes resolved facts onto `account` and records new conflicts. Returns conflicts recorded."""
    rows = []
    for account_id, row in facts[facts["has_facts"]].iterrows():
        rows.append({"aid": account_id, "owner": row["owner_user_id"],
                     "seg": row["segment"] if isinstance(row["segment"], str) else None,
                     "arr": float(row["arr"]) if pd.notna(row["arr"]) else None,
                     "rd": row["renewal_date"].date(), "status": row["status"]})
    if rows:
        conn.execute(text("UPDATE account SET owner_user_id = :owner, segment = :seg, arr = :arr, "
                          "renewal_date = :rd, status = :status, facts_resolved_at = now() WHERE id = :aid"), rows)
    existing = {(r.account_id, r.fact, r.chosen_value, r.other_value) for r in conn.execute(text(
        "SELECT account_id, fact, chosen_value, other_value FROM account_fact_conflict"))}
    new = [c for c in conflicts_for(facts)
           if (c["account_id"], c["fact"], c["chosen_value"], c["other_value"]) not in existing]
    if new:
        conn.execute(t.account_fact_conflict.insert(),
                     [{**c, "id": t.new_id(), "tenant_id": tenant_id, "detected_run_id": run_id} for c in new])
    return len(new)


def grace_days(contract_type: str) -> timedelta:
    return timedelta(days=GRACE.get(contract_type, 90))
