"""Training/evaluation examples: point-in-time snapshots with labels (research R5, R7).

Each example is (account, cutoff C) for an active account whose renewal falls in (C, C+90], with
signals computed only from data received and linked before C. Labels come from renewal outcomes
recorded before `as_of`. Examples are split by renewal date: train < valid < test (~last 6 months
of fully labelled renewals), so no test outcome is known at training time.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time

import numpy as np
import pandas as pd
from sqlalchemy import Connection, text

from worklist.signals.compute import compute_snapshot, in_horizon, insufficient
from worklist.signals.events import load_accounts, load_events, load_users
from worklist.signals.registry import FEATURES

HORIZON = pd.Timedelta(days=90)
TEST_DAYS, VALID_DAYS = 182, 120
RECENT_WINDOW = pd.Timedelta(days=548)  # ~18 months (FR-012b)


@dataclass
class Windows:
    as_of: pd.Timestamp
    label_end: pd.Timestamp
    test_start: pd.Timestamp
    valid_start: pd.Timestamp
    first_cutoff: pd.Timestamp

    def as_json(self) -> dict:
        return {k: v.date().isoformat() for k, v in self.__dict__.items()}


def windows_for(as_of: pd.Timestamp, history_start: pd.Timestamp) -> Windows:
    label_end = as_of - HORIZON
    test_start = label_end - pd.Timedelta(days=TEST_DAYS)
    return Windows(as_of, label_end, test_start, test_start - pd.Timedelta(days=VALID_DAYS),
                   history_start + pd.Timedelta(days=120))


def ts(day) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, time(0), tzinfo=UTC))


def load_outcomes(conn: Connection, as_of: pd.Timestamp) -> pd.DataFrame:
    rows = conn.execute(text(
        "SELECT o.account_id, o.renewal_date, o.outcome, o.is_silent_churn, a.contract_type "
        "FROM renewal_outcome o JOIN account a ON a.id = o.account_id WHERE o.recorded_at <= :a"),
        {"a": as_of.to_pydatetime()}).mappings().all()
    df = pd.DataFrame(rows, columns=["account_id", "renewal_date", "outcome", "is_silent_churn", "contract_type"])
    df["renewal_date"] = pd.to_datetime(df["renewal_date"]).dt.tz_localize("UTC")
    return df


def history_start(conn: Connection) -> pd.Timestamp:
    first = conn.execute(text("SELECT min(occurred_at) FROM raw_event")).scalar()
    return pd.Timestamp(first).normalize()


def build_tenant_examples(conn: Connection, tenant_id, w: Windows, step_days: int = 7) -> pd.DataFrame:
    """All labelled in-horizon examples for one tenant (RLS-scoped connection)."""
    events = load_events(conn, w.as_of.to_pydatetime())
    accounts, users = load_accounts(conn), load_users(conn)
    outcomes = load_outcomes(conn, w.as_of)
    monthly = set(accounts.index[accounts["contract_type"] == "monthly"])
    frames = []
    cutoff = w.first_cutoff
    while cutoff < w.label_end:
        snap = compute_snapshot(events, accounts, users, cutoff)
        keep = in_horizon(snap) & ~insufficient(snap)
        if keep.any():
            df = snap.signals[keep].copy()
            df["owner_user_id"] = snap.facts.loc[keep, "owner_user_id"]
            df["arr"] = snap.facts.loc[keep, "arr"]
            df["cutoff"] = cutoff
            df["low_confidence"] = snap.low_confidence[keep]
            frames.append(df.reset_index(names="account_id"))
        cutoff += pd.Timedelta(days=step_days)
    if not frames:
        return pd.DataFrame()
    ex = pd.concat(frames, ignore_index=True)
    ex["tenant_id"] = tenant_id

    # Label: the first recorded outcome with renewal date in (C, C+90].
    oc = outcomes.sort_values("renewal_date")
    merged = ex[["account_id", "cutoff"]].reset_index().merge(oc, on="account_id", how="left")
    in_win = (merged["renewal_date"] > merged["cutoff"]) & (merged["renewal_date"] <= merged["cutoff"] + HORIZON)
    first = merged[in_win].sort_values("renewal_date").drop_duplicates("index").set_index("index")
    ex["renewal_date"] = first["renewal_date"].reindex(ex.index)
    ex["label"] = (first["outcome"].reindex(ex.index) == "churned").astype(float)
    ex["is_silent_churn"] = first["is_silent_churn"].reindex(ex.index).fillna(False).astype(bool)
    # Monthly plans record only cancellations: no cancellation in the window = renewed, once known.
    is_monthly = ex["account_id"].isin(monthly)
    no_outcome = ex["renewal_date"].isna()
    ex.loc[is_monthly & no_outcome, "label"] = 0.0
    ex.loc[is_monthly & no_outcome, "renewal_date"] = ex.loc[is_monthly & no_outcome, "cutoff"] + HORIZON
    ex = ex[ex["renewal_date"].notna() & (ex["renewal_date"] <= w.label_end)].copy()
    ex["split"] = np.select([ex["renewal_date"] > w.test_start, ex["renewal_date"] > w.valid_start],
                            ["test", "valid"], "train")
    return ex.reset_index(drop=True)


def training_rows(ex: pd.DataFrame, history_window: str, w: Windows) -> pd.DataFrame:
    """Train split at 14-day spacing; `recent_18m` keeps only the last ~18 months before validation."""
    train = ex[(ex["split"] == "train") & (((ex["cutoff"] - w.first_cutoff).dt.days // 7) % 2 == 0)]
    if history_window == "recent_18m":
        train = train[train["cutoff"] >= w.valid_start - RECENT_WINDOW]
    return train


def X(df: pd.DataFrame) -> pd.DataFrame:
    return df[FEATURES].astype(float)
