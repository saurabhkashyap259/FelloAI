"""Loads an account-linked event frame for one tenant, with the payload fields signals need.

Point-in-time filtering (`as_of`) keeps only rows with ingested_at < C and matched_at < C, so
results at cutoff C never change when later records or later matches arrive (FR-007).
"""

from datetime import datetime

import pandas as pd
from sqlalchemy import Connection, text

FIELDS = {
    "active_users": "float", "sessions": "float", "seats_purchased": "float", "seats_assigned": "float",
    "priority": "str", "satisfaction": "str", "ticket_id": "str", "attempt": "float", "mrr": "float",
    "current_period_end": "str", "cancel_at_period_end": "str", "change": "str", "role": "str", "stage": "str",
    "amount": "float", "owner_email": "str", "arr": "float", "renewal_date": "str", "segment": "str",
    "is_csm": "str", "direction": "str", "invoice_id": "str",
}


def load_events(conn: Connection, until: datetime) -> pd.DataFrame:
    """All linked events of the connection's tenant ingested before `until` (RLS scopes the tenant)."""
    cols = ", ".join(f"r.payload->>'{f}' AS {f}" for f in FIELDS)
    rows = conn.execute(text(
        f"SELECT r.id AS raw_event_id, ea.account_id, r.source, r.event_type, r.occurred_at, r.ingested_at, "
        f"ea.matched_at, {cols} FROM raw_event r JOIN event_account ea ON ea.raw_event_id = r.id "
        f"WHERE r.ingested_at < :u AND ea.matched_at < :u"), {"u": until}).mappings().all()
    df = pd.DataFrame(rows, columns=["raw_event_id", "account_id", "source", "event_type", "occurred_at",
                                     "ingested_at", "matched_at", *FIELDS])
    for name, kind in FIELDS.items():
        if kind == "float":
            df[name] = pd.to_numeric(df[name], errors="coerce")
    for col in ("occurred_at", "ingested_at", "matched_at"):
        df[col] = pd.to_datetime(df[col], utc=True)
    df["cancel_at_period_end"] = df["cancel_at_period_end"].map({"true": True, "false": False})
    return df


def as_of(events: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    return events[(events["ingested_at"] < cutoff) & (events["matched_at"] < cutoff)]


def load_accounts(conn: Connection) -> pd.DataFrame:
    rows = conn.execute(text(
        "SELECT id, name, contract_type, contract_start, segment AS master_segment, primary_domain "
        "FROM account")).mappings().all()
    df = pd.DataFrame(rows, columns=["id", "name", "contract_type", "contract_start", "master_segment",
                                     "primary_domain"])
    df["contract_start"] = pd.to_datetime(df["contract_start"], utc=True)
    return df.set_index("id")


def load_users(conn: Connection) -> dict[str, object]:
    return {r.email.lower(): r.id for r in conn.execute(text("SELECT id, email FROM app_user"))}
