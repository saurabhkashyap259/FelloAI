"""Point-in-time signal computation (FR-007, FR-010, research R5/R8).

`compute_snapshot` takes the tenant's event frame, keeps only what was received and linked before
the cutoff, and returns one row per account: signal values, facts, missing sources, stale-source
flags and the inputs for state and confidence. Windows are measured on business time (occurred_at).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from worklist.ingest.facts import resolve_facts
from worklist.signals.events import as_of
from worklist.signals.registry import FEATURES, SIGNALS, SOURCES

DAY = pd.Timedelta(days=1)
SPARSE_RECORDS = 5
NEW_ACCOUNT_DAYS = 60
LOW_CONFIDENCE_MISSING = 0.30
HORIZON = 90
# Sources an account "normally has": absence there means unknown (NaN), never "no activity".
EXPECTED_SOURCES = ("product", "billing")


@dataclass
class Snapshot:
    cutoff: pd.Timestamp
    signals: pd.DataFrame  # index account_id, columns FEATURES
    facts: pd.DataFrame
    missing_sources: pd.Series  # list per account
    stale_sources: list[str]
    records_180d: pd.Series
    has_usage: pd.Series
    low_confidence: pd.Series


def stale_sources(events: pd.DataFrame, cutoff: pd.Timestamp) -> list[str]:
    """A source is stale when nothing arrived for longer than max(2 days, 3× its median delivery gap
    over the previous 30 days) (spec Definitions)."""
    stale = []
    for source in SOURCES:
        times = events.loc[events["source"] == source, "ingested_at"]
        if times.empty:
            continue
        # Median gap over the previous 30 days; sparse feeds need a longer sample to estimate it.
        window = times[times >= cutoff - 30 * DAY].sort_values()
        if len(window) < 20:
            window = times[times >= cutoff - 180 * DAY].sort_values()
        gaps = window.diff().dropna()
        median = gaps.median() if len(gaps) else pd.Timedelta(days=1)
        if cutoff - times.max() > max(pd.Timedelta(days=2), 3 * median):
            stale.append(source)
    return stale


def _count(ev: pd.DataFrame, idx: pd.Index, mask: pd.Series) -> pd.Series:
    return ev[mask].groupby("account_id").size().reindex(idx, fill_value=0).astype(float)


def compute_snapshot(events: pd.DataFrame, accounts: pd.DataFrame, users_by_email: dict,
                     cutoff: pd.Timestamp) -> Snapshot:
    ev = as_of(events, cutoff)
    idx = accounts.index
    facts = resolve_facts(ev, accounts, users_by_email, cutoff)
    age = (cutoff - ev["occurred_at"]).dt.days
    within = {w: (age >= 0) & (age < w) for w in (30, 90, 120, 180, 365)}
    et = ev["event_type"]
    s = pd.DataFrame(index=idx, dtype=float)

    # --- product
    usage = ev[et == "usage.summary"].sort_values("occurred_at")
    seats = ev[et == "seats.changed"].sort_values("occurred_at").groupby("account_id").tail(1).set_index("account_id")
    last_usage = usage.groupby("account_id").tail(1).set_index("account_id")
    usage = usage.assign(rel=(usage.groupby("account_id")["occurred_at"].transform("max")
                              - usage["occurred_at"]).dt.days)
    earlier = (usage[(usage["rel"] >= 40) & (usage["rel"] <= 130)]
               .groupby("account_id")[["active_users", "sessions"]].mean())
    fresh = last_usage[(cutoff - last_usage["occurred_at"]).dt.days <= 120]
    purchased = seats["seats_purchased"].reindex(idx)
    s["active_user_ratio"] = (fresh["active_users"].reindex(idx) / purchased).clip(upper=2)
    s["usage_trend_60d"] = (fresh["active_users"].reindex(idx)
                            / earlier["active_users"].reindex(idx).replace(0, np.nan)) - 1
    s["sessions_trend_60d"] = (fresh["sessions"].reindex(idx) / earlier["sessions"].reindex(idx).replace(0, np.nan)) - 1
    s["sessions_per_user"] = fresh["sessions"].reindex(idx) / fresh["active_users"].reindex(idx).replace(0, np.nan)
    s["days_since_usage"] = (cutoff - last_usage["occurred_at"]).dt.days.reindex(idx).astype(float)
    s["seat_utilisation"] = seats["seats_assigned"].reindex(idx) / purchased

    # --- support
    created = ev[et == "ticket.created"]
    hi = created["priority"].isin(["high", "urgent"])
    s["tickets_30d"] = _count(created, idx, within[30][created.index])
    s["tickets_90d"] = _count(created, idx, within[90][created.index])
    s["high_urgent_tickets_30d"] = _count(created, idx, within[30][created.index] & hi)
    s["high_urgent_tickets_90d"] = _count(created, idx, within[90][created.index] & hi)
    s["urgent_tickets_30d"] = _count(created, idx, within[30][created.index] & (created["priority"] == "urgent"))
    prior = _count(created, idx, (age[created.index] >= 30) & (age[created.index] < 120))
    s["ticket_trend"] = s["tickets_30d"] / (prior / 3 + 0.5)
    solved = ev[et == "ticket.solved"].drop_duplicates("ticket_id").set_index("ticket_id")["occurred_at"]
    c90 = created[within[90][created.index]]
    joined = c90[["account_id", "ticket_id", "occurred_at"]].merge(
        solved.rename("solved_at").reset_index(), on="ticket_id", how="left")
    joined["days"] = (joined["solved_at"] - joined["occurred_at"]).dt.total_seconds() / 86400
    s["median_resolution_days_90d"] = joined.groupby("account_id")["days"].median().reindex(idx)
    c180 = created[within[180][created.index]]
    s["open_tickets"] = _count(c180, idx, ~c180["ticket_id"].isin(solved.index))
    solved_ev = ev[et == "ticket.solved"]
    s["bad_csat_90d"] = _count(solved_ev, idx, within[90][solved_ev.index] & (solved_ev["satisfaction"] == "bad"))

    # --- billing
    failed = ev[et == "invoice.payment_failed"]
    f90 = failed[within[90][failed.index]]
    s["payment_failures_90d"] = _count(failed, idx, within[90][failed.index])
    s["max_payment_attempt_90d"] = f90.groupby("account_id")["attempt"].max().reindex(idx).fillna(0)
    s["cancel_at_period_end"] = facts["cancel_at_period_end"].astype(float)
    paid = ev[et == "invoice.paid"].groupby("account_id")["occurred_at"].max()
    s["days_since_invoice_paid"] = (cutoff - paid).dt.days.reindex(idx).astype(float)

    # --- crm
    contacts = ev[et == "contact.changed"]
    c_in = within[90][contacts.index]
    s["champion_removed_90d"] = _count(contacts, idx, c_in & (contacts["change"] == "removed")
                                       & (contacts["role"] == "champion"))
    s["net_contacts_change_90d"] = (_count(contacts, idx, c_in & (contacts["change"] == "added"))
                                    - _count(contacts, idx, c_in & (contacts["change"] == "removed")))
    opps = ev[et == "opportunity.updated"]
    s["opp_at_risk_90d"] = _count(opps, idx, within[90][opps.index] & (opps["stage"] == "renewal_at_risk"))

    # --- email
    sent = ev[(et == "email.sent")]
    last_csm = sent.groupby("account_id")["occurred_at"].max()
    s["days_since_csm_email"] = (cutoff - last_csm).dt.days.reindex(idx).astype(float).clip(upper=365)
    s["csm_emails_90d"] = _count(sent, idx, within[90][sent.index])
    received = ev[et == "email.received"]
    s["customer_emails_90d"] = _count(received, idx, within[90][received.index])
    bounced = ev[et == "email.bounced"]
    s["bounces_90d"] = _count(bounced, idx, within[90][bounced.index])

    # --- account
    s["days_to_renewal"] = facts["days_to_renewal"].astype(float)
    s["tenure_days"] = (cutoff - accounts["contract_start"]).dt.days.astype(float)
    s["log_arr"] = np.log1p(facts["arr"].astype(float))
    s["is_monthly"] = (accounts["contract_type"] == "monthly").astype(float)

    # Never-contacted accounts: absence since the start is a real value, capped like the rest.
    never = s["days_since_csm_email"].isna()
    s.loc[never, "days_since_csm_email"] = s.loc[never, "tenure_days"].clip(upper=365)

    # Missing sources (no records in 180 days) and stale tenant feeds → unknown, not "no activity".
    recent = ev[within[180]]
    present = {src: set(recent.loc[recent["source"] == src, "account_id"]) for src in SOURCES}
    # Term contracts are invoiced yearly, so billing counts as present if seen within ~13 months.
    present["billing"] = set(ev.loc[(age >= 0) & (age < 400) & (ev["source"] == "billing"), "account_id"])
    stale = stale_sources(events[events["ingested_at"] < cutoff], cutoff)
    by_source: dict[str, list[str]] = {}
    for sig in SIGNALS:
        by_source.setdefault(sig.source, []).append(sig.name)
    for src in SOURCES:
        absent = ~idx.isin(list(present[src]))
        if src in stale or src in EXPECTED_SOURCES:
            cols = [c for c in by_source.get(src, []) if c != "cancel_at_period_end"]
            mask = absent if src not in stale else np.ones(len(idx), dtype=bool)
            s.loc[mask, cols] = np.nan
    missing = pd.Series([[src for src in SOURCES if acc not in present[src]] for acc in idx], index=idx)

    records_180d = recent.groupby("account_id").size().reindex(idx, fill_value=0)
    has_usage = idx.isin(list(present["product"]))
    # Share of signals unknown because a source is missing (no-ticket resolution time is not "missing").
    missing_share = s[[f for f in FEATURES if f != "median_resolution_days_90d"]].isna().mean(axis=1)
    had_365 = {src: set(ev.loc[within[365] & (ev["source"] == src), "account_id"]) for src in SOURCES}
    stale_hit = pd.Series(False, index=idx)
    for src in stale:
        stale_hit |= idx.isin(list(had_365[src]))
    low_conf = (missing_share > LOW_CONFIDENCE_MISSING) | stale_hit
    return Snapshot(cutoff, s[FEATURES], facts, missing, stale, records_180d,
                    pd.Series(has_usage, index=idx), low_conf)


def insufficient(snap: Snapshot) -> pd.Series:
    """Spec Definitions: <5 matched records in 180 days, or younger than 60 days with no usage."""
    young = snap.signals["tenure_days"] < NEW_ACCOUNT_DAYS
    return (snap.records_180d < SPARSE_RECORDS) | (young & ~snap.has_usage)


def in_horizon(snap: Snapshot) -> pd.Series:
    d = snap.facts["days_to_renewal"]
    return (snap.facts["status"] == "active") & (d >= 0) & (d <= HORIZON)
