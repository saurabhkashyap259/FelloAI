"""Scoring one tenant at one cutoff: states, calibrated probabilities, contributions, reasons, bands.

Writes immutable `signal_snapshot` and `account_score` rows; these are the only source for what the
worklist displays (FR-020). The LLM is never involved here (FR-008).
"""

import json
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import Connection, text

from worklist.db import tables as t
from worklist.model.bands import assign_bands, assign_confidence, assign_states
from worklist.model.calibrate import apply_calibrator
from worklist.model.registry import load_approved
from worklist.signals.compute import Snapshot, compute_snapshot, insufficient
from worklist.signals.events import load_accounts, load_events, load_users
from worklist.signals.registry import BY_NAME, FEATURES, SIGNAL_DEFS_VERSION, reason_text

REASON_KINDS_EXCLUDED = {"attribute"}  # "renewal in 30 days" explains timing, not risk; kept in contributions


@dataclass
class ScoreResult:
    scores: pd.DataFrame  # index account_id: state, probability, band, rank, owner, arr, days_to_renewal, score_id
    snapshot: Snapshot
    model_version_id: object
    counts: dict


def _clean(value) -> float | None:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return float(value)


def build_reasons(contrib_row: np.ndarray, values: pd.Series) -> tuple[list[dict], list[dict]]:
    contributions: list[dict[str, Any]] = []
    for i, name in enumerate(FEATURES):
        contributions.append({"signal": name, "value": _clean(values[name]),
                              "contribution": round(float(contrib_row[i]), 4)})
    contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)
    reasons = []
    for c in contributions:
        sig = BY_NAME[c["signal"]]
        if sig.kind in REASON_KINDS_EXCLUDED or c["contribution"] <= 0.02:
            continue
        reasons.append({"signal": sig.name, "text": reason_text(sig, c["value"], c["contribution"]),
                        "direction": "up", "contribution": c["contribution"]})
        if len(reasons) == 3:
            break
    return contributions, reasons


def score_tenant(conn: Connection, tenant_id, cutoff: pd.Timestamp, run_id, settings: dict,
                 events: pd.DataFrame | None = None) -> ScoreResult:
    if events is None:
        events = load_events(conn, cutoff.to_pydatetime())
    accounts, users = load_accounts(conn), load_users(conn)
    snap = compute_snapshot(events, accounts, users, cutoff)
    facts = snap.facts
    live = facts.index[~facts["status"].isin(["churned", "not_started"])]
    approved = load_approved(conn)
    has_model = approved is not None and approved.signal_defs_version == SIGNAL_DEFS_VERSION

    state = assign_states(facts.loc[live, "status"], facts.loc[live, "days_to_renewal"],
                          insufficient(snap).loc[live], has_model)
    confidence = assign_confidence(state, snap.low_confidence.loc[live])
    out = pd.DataFrame({"state": state, "confidence": confidence,
                        "owner_user_id": facts.loc[live, "owner_user_id"], "arr": facts.loc[live, "arr"],
                        "days_to_renewal": facts.loc[live, "days_to_renewal"]})
    out["probability"] = np.nan
    out["raw_score"] = np.nan
    out["band"] = None
    out["rank"] = None
    contributions: dict = {}
    reasons: dict = {}

    scored = out.index[out["state"] == "scored"]
    if len(scored):
        X = snap.signals.loc[scored, FEATURES].astype(float)
        raw = approved.model.raw_score(X)
        out.loc[scored, "raw_score"] = raw
        out.loc[scored, "probability"] = apply_calibrator(approved.calibrator, raw)
        contrib = approved.model.contributions(X)
        for i, account_id in enumerate(scored):
            contributions[account_id], reasons[account_id] = build_reasons(contrib[i], X.iloc[i])
        band, rank = assign_bands(out.loc[scored, "probability"], out.loc[scored, "owner_user_id"],
                                  int(settings["high_band_size"]), float(settings["high_band_floor"]))
        out.loc[scored, "band"] = band
        out.loc[scored, "rank"] = rank

    snapshot_rows, score_rows = [], []
    for account_id, row in out.iterrows():
        values = {k: _clean(v) for k, v in snap.signals.loc[account_id].items()}
        snapshot_rows.append({"run_id": run_id, "account_id": account_id, "tenant_id": tenant_id,
                              "cutoff_at": cutoff.to_pydatetime(), "signals": values,
                              "signal_defs_version": SIGNAL_DEFS_VERSION,
                              "missing_sources": list(snap.missing_sources.loc[account_id])})
        score_id = t.new_id()
        out.loc[account_id, "score_id"] = score_id
        dtr = row["days_to_renewal"]
        score_rows.append({
            "id": score_id, "tenant_id": tenant_id, "run_id": run_id, "account_id": account_id,
            "owner_user_id": row["owner_user_id"] if isinstance(row["owner_user_id"], type(account_id)) else None,
            "state": row["state"], "model_version_id": approved.id if has_model else None,
            "signal_defs_version": SIGNAL_DEFS_VERSION, "raw_score": _clean(row["raw_score"]),
            "probability": _clean(row["probability"]), "band": row["band"],
            "rank_in_owner": int(row["rank"]) if row["rank"] is not None and not pd.isna(row["rank"]) else None,
            "confidence": row["confidence"], "contributions": contributions.get(account_id, []),
            "reasons": reasons.get(account_id, []),
            "days_to_renewal": int(dtr) if not pd.isna(dtr) else None, "arr": _clean(row["arr"]),
            "cutoff_at": cutoff.to_pydatetime(),
        })
    for i in range(0, len(snapshot_rows), 2000):
        conn.execute(t.signal_snapshot.insert(), snapshot_rows[i:i + 2000])
        conn.execute(t.account_score.insert(), score_rows[i:i + 2000])
    counts = {"scored": int((out["state"] == "scored").sum()),
              "insufficient": int((out["state"] == "insufficient_data").sum()),
              "not_due": int((out["state"] == "not_due").sum()),
              "renewal_overdue": int((out["state"] == "renewal_overdue").sum()),
              "no_model": int((out["state"] == "no_model").sum()),
              "low_confidence": int((out["confidence"] == "low").sum()),
              "stale_sources": snap.stale_sources}
    return ScoreResult(out, snap, approved.id if has_model else None, counts)


def settings_for(conn: Connection) -> dict:
    return dict(conn.execute(text("SELECT * FROM tenant_settings")).mappings().first())


def to_json(value) -> str:
    return json.dumps(value, default=str)


def recompute_score(conn: Connection, score_id) -> dict:
    """Recomputes a stored score from raw records, links and the referenced model version (FR-002).

    Used to prove reproducibility: the result must equal what was stored."""
    from worklist.model.registry import load_version

    row = conn.execute(text("SELECT s.*, c.method, c.params FROM account_score s "
                            "JOIN tenant_calibrator c ON c.model_version_id = s.model_version_id "
                            "WHERE s.id = :id"), {"id": score_id}).mappings().first()
    cutoff = pd.Timestamp(row["cutoff_at"])
    events = load_events(conn, cutoff.to_pydatetime())
    snap = compute_snapshot(events, load_accounts(conn), load_users(conn), cutoff)
    model, _ = load_version(conn, row["model_version_id"])
    X = snap.signals.loc[[row["account_id"]], FEATURES].astype(float)
    raw = model.raw_score(X)
    probability = float(apply_calibrator({"method": row["method"], "params": row["params"]["calibrator"]}, raw)[0])
    contributions, _ = build_reasons(model.contributions(X)[0], X.iloc[0])
    return {"probability": probability, "raw_score": float(raw[0]), "contributions": contributions}
