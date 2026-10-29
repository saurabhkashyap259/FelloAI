"""Alert triggers, suppression, caps and deduplication (FR-017, FR-017a, FR-017b, spec Definitions).

Triggers (material changes only):
  - entered_top_k: in the owner's high band now, not in it at the previous successful run;
  - risk_jump: calibrated probability ≥ threshold above the score ~7 days earlier;
  - severe_event_near_renewal: renewal within the window and a newly received severe event.
Snooze/dismiss suppress a trigger unless it is of a new type, or a further jump since the action.
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd
from sqlalchemy import Connection, text

from worklist.agent.evidence import build_evidence
from worklist.db import tables as t
from worklist.domain import TRIGGER_PRIORITY, Trigger

REPEAT_WINDOW_DAYS = 30


@dataclass
class Candidate:
    account_id: object
    trigger: str
    fingerprint: str
    detail: dict
    owner: object
    probability: float
    arr: float
    score_id: object
    extra: dict = field(default_factory=dict)

    @property
    def priority(self) -> int:
        return TRIGGER_PRIORITY[Trigger(self.trigger)]


def _previous_scores(conn: Connection, before: pd.Timestamp, on_or_before: bool = False) -> dict:
    op = "<=" if on_or_before else "<"
    run = conn.execute(text(
        f"SELECT id FROM pipeline_run WHERE status = 'succeeded' AND run_type IN ('nightly', 'backfill') "
        f"AND cutoff_at {op} :c ORDER BY cutoff_at DESC, (run_type = 'nightly') DESC LIMIT 1"),
        {"c": before.to_pydatetime()}).scalar()
    if run is None:
        return {}
    rows = conn.execute(text("SELECT account_id, band, probability FROM account_score WHERE run_id = :r"), {"r": run})
    return {r.account_id: {"band": r.band, "probability": r.probability} for r in rows}


SEVERE_LABELS = {
    "urgent_ticket": "urgent support ticket", "high_tickets_7d": "2+ high-priority tickets within 7 days",
    "final_payment_retry": "payment failed on the final retry", "cancel_at_period_end": "subscription set to cancel",
    "champion_removed": "champion contact removed with no replacement",
}


def severe_events(events: pd.DataFrame, account_id, since: pd.Timestamp, cutoff: pd.Timestamp) -> list[tuple[str, str]]:
    """(kind, fingerprint) for severe conditions whose evidence was received in (since, cutoff]."""
    ev = events[(events["account_id"] == account_id) & (events["ingested_at"] < cutoff)
                & (events["matched_at"] < cutoff)]
    new = ev[ev["ingested_at"] >= since]
    found = []
    for _, row in new.iterrows():
        et = row["event_type"]
        if et == "ticket.created" and row["priority"] == "urgent":
            found.append(("urgent_ticket", f"urgent:{row['raw_event_id']}"))
        elif et == "invoice.payment_failed" and (row["attempt"] or 0) >= 4:
            found.append(("final_payment_retry", f"final_retry:{row['raw_event_id']}"))
        elif et == "subscription.updated" and row["cancel_at_period_end"] is True:
            found.append(("cancel_at_period_end", f"cancel:{row['current_period_end']}"))
        elif et == "contact.changed" and row["change"] == "removed" and row["role"] == "champion":
            later_added = ev[(ev["event_type"] == "contact.changed") & (ev["change"] == "added")
                             & (ev["occurred_at"] > row["occurred_at"])]
            if later_added.empty:
                found.append(("champion_removed", f"champion:{row['raw_event_id']}"))
    high = ev[(ev["event_type"] == "ticket.created") & ev["priority"].isin(["high", "urgent"])
              & (ev["occurred_at"] >= cutoff - pd.Timedelta(days=7))]
    if len(high) >= 2 and (high["ingested_at"] >= since).any():
        found.append(("high_tickets_7d", f"high7:{sorted(high['raw_event_id'].astype(str))[0]}"))
    return found


def find_candidates(conn: Connection, cutoff: pd.Timestamp, result, events: pd.DataFrame,
                    settings: dict) -> list[Candidate]:
    scores = result.scores
    facts = result.snapshot.facts
    prev_run_cutoff = conn.execute(text(
        "SELECT max(cutoff_at) FROM pipeline_run WHERE status = 'succeeded' AND run_type IN ('nightly', 'backfill') "
        "AND cutoff_at < :c"), {"c": cutoff.to_pydatetime()}).scalar()
    previous = _previous_scores(conn, cutoff)
    week_ago = _previous_scores(conn, cutoff - pd.Timedelta(days=7), on_or_before=True)
    since = pd.Timestamp(prev_run_cutoff) if prev_run_cutoff else cutoff - pd.Timedelta(days=1)
    jump = float(settings["alert_risk_jump"])
    window = int(settings["renewal_alert_window_days"])
    out: list[Candidate] = []
    for account_id, row in scores.iterrows():
        common = {"account_id": account_id, "owner": row["owner_user_id"] if isinstance(row["owner_user_id"],
                                                                                       type(account_id)) else None,
                  "probability": float(row["probability"]) if pd.notna(row["probability"]) else 0.0,
                  "arr": float(row["arr"]) if pd.notna(row["arr"]) else 0.0, "score_id": row["score_id"]}
        if row["state"] == "scored":
            prev = previous.get(account_id)
            if previous and row["band"] == "high" and (prev is None or prev["band"] != "high"):
                out.append(Candidate(trigger=Trigger.ENTERED_TOP_K.value, fingerprint="entered_top_k",
                                     detail={"previous_band": prev["band"] if prev else None, "current_rank":
                                             int(row["rank"]), "probability": round(common["probability"], 4)},
                                     **common))
            then = week_ago.get(account_id)
            if then and then["probability"] is not None and common["probability"] - then["probability"] >= jump:
                level = int(common["probability"] / jump)
                out.append(Candidate(trigger=Trigger.RISK_JUMP.value, fingerprint=f"level{level}",
                                     detail={"previous_probability": round(then["probability"], 4),
                                             "probability": round(common["probability"], 4)}, **common))
        days = facts.loc[account_id, "days_to_renewal"]
        if row["state"] in ("scored", "insufficient_data", "no_model") and pd.notna(days) and 0 <= days <= window:
            for kind, fingerprint in severe_events(events, account_id, since, cutoff):
                out.append(Candidate(trigger=Trigger.SEVERE_EVENT_NEAR_RENEWAL.value, fingerprint=fingerprint,
                                     detail={"event": kind, "label": SEVERE_LABELS[kind], "days_to_renewal": int(days)},
                                     **common))
    return out


def _suppressed(conn: Connection, cand: Candidate, today, jump: float, cutoff: pd.Timestamp) -> bool:
    recent = conn.execute(text(
        "SELECT 1 FROM alert WHERE account_id = :a AND trigger = :t AND fingerprint = :f AND created_at > :since"),
        {"a": cand.account_id, "t": cand.trigger, "f": cand.fingerprint,
         "since": (cutoff - pd.Timedelta(days=REPEAT_WINDOW_DAYS)).to_pydatetime()}).first()
    if recent:
        return True  # same unchanged condition (FR-017)
    action = conn.execute(text(
        "SELECT action, snooze_until, fingerprint, probability_at_action FROM csm_action WHERE account_id = :a "
        "AND action IN ('snoozed', 'dismissed') ORDER BY created_at DESC LIMIT 1"), {"a": cand.account_id}).first()
    if action is None or (action.action == "snoozed" and action.snooze_until <= today):
        return False
    active_types = set((action.fingerprint or {}).get("trigger_types", []))
    if cand.trigger not in active_types:
        return False  # a different kind of material change overrides the snooze/dismiss
    if cand.trigger == Trigger.RISK_JUMP and action.probability_at_action is not None:
        return cand.probability - action.probability_at_action < jump
    return True


def raise_alerts(conn: Connection, tenant_id, run_id, cutoff: pd.Timestamp, result, events: pd.DataFrame,
                 settings: dict) -> dict:
    today = (cutoff - pd.Timedelta(days=0)).date()
    jump = float(settings["alert_risk_jump"])
    candidates = [c for c in find_candidates(conn, cutoff, result, events, settings)
                  if not _suppressed(conn, c, today, jump, cutoff)]
    # One alert per account per night: keep its highest-priority trigger.
    best: dict = {}
    for c in sorted(candidates, key=lambda c: (c.priority, -c.probability)):
        best.setdefault(c.account_id, c)
    by_owner: dict = {}
    for c in best.values():
        by_owner.setdefault(c.owner, []).append(c)
    raised, suppressed = [], 0
    for owner, items in by_owner.items():
        items.sort(key=lambda c: (c.priority, -(c.probability * c.arr)))
        week = conn.execute(text(
            "SELECT count(*) FROM alert WHERE raised_for_user_id IS NOT DISTINCT FROM :u AND created_at > :since"),
            {"u": owner, "since": (cutoff - pd.Timedelta(days=7)).to_pydatetime()}).scalar()
        room = max(0, min(int(settings["alert_cap_nightly"]), int(settings["alert_cap_weekly"]) - week))
        raised += items[:room]
        suppressed += max(0, len(items) - room)
    facts = result.snapshot.facts
    for c in raised:
        score = {"days_to_renewal": facts.loc[c.account_id, "days_to_renewal"],
                 "renewal_date": facts.loc[c.account_id, "renewal_date"].date().isoformat()
                 if pd.notna(facts.loc[c.account_id, "renewal_date"]) else None, "arr": c.arr}
        evidence = build_evidence(events, c.account_id, cutoff, score)
        conn.execute(text(
            "INSERT INTO alert (id, tenant_id, run_id, account_id, raised_for_user_id, score_id, priority, trigger, "
            "trigger_detail, fingerprint, evidence, dedupe_key, status, created_at) VALUES (:id, :t, :r, :a, :u, :s, "
            ":p, :tr, :d, :f, :e, :k, 'open', :created) ON CONFLICT (dedupe_key) DO NOTHING"),
            {"id": t.new_id(), "t": tenant_id, "r": run_id, "a": c.account_id, "u": c.owner, "s": c.score_id,
             "p": c.priority, "tr": c.trigger, "d": json.dumps(c.detail, default=str), "f": c.fingerprint,
             "e": json.dumps(evidence, default=str),
             "k": f"{c.account_id}:{c.trigger}:{c.fingerprint}:{cutoff.date().isoformat()}",
             "created": (cutoff - timedelta(seconds=1)).to_pydatetime()})
    return {"alerts_raised": len(raised), "alerts_suppressed": suppressed,
            "alert_candidates": len(candidates)}
