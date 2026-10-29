"""Feed delivery: the connector shim. Moves due records from the pending feed through the same
ingestion code a real connector would call: normalise → dedupe insert → identity → link or quarantine.

Runs inside a tenant-scoped job connection, so RLS confines it to one tenant.
"""

from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, bindparam, text
from sqlalchemy.dialects.postgresql import insert

from worklist.db import tables as t
from worklist.ingest.identity import AccountIndex
from worklist.ingest.normalize import normalize

BATCH = 5000
AGED_AFTER = timedelta(days=30)


def ingest_envelopes(conn: Connection, tenant_id: UUID, envelopes: list[dict], index: AccountIndex,
                     run_id: UUID | None, source_timezones: dict) -> Counter:
    """Ingests a batch of envelopes {source, source_record_id, event_type, occurred_at, ingested_at, payload}."""
    counts: Counter = Counter()
    rows, by_key = [], {}
    for env in envelopes:
        norm = normalize(env["source"], env.get("source_record_id"), env["event_type"], env.get("occurred_at"),
                         env["ingested_at"], env["payload"], source_timezones)
        key = (env["source"], norm.dedupe_key)
        if key in by_key:
            counts["duplicates"] += 1
            continue
        row = {"id": t.new_id(), "tenant_id": tenant_id, "source": env["source"],
               "source_record_id": env.get("source_record_id"), "dedupe_key": norm.dedupe_key,
               "event_type": env["event_type"], "occurred_at": norm.occurred_at, "ingested_at": env["ingested_at"],
               "payload": norm.payload}
        by_key[key] = (row, norm.invalid_reason)
        rows.append(row)
    if not rows:
        return counts
    inserted_ids: set = set()
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        stmt = insert(t.raw_event).on_conflict_do_nothing(
            index_elements=["tenant_id", "source", "dedupe_key"]).returning(t.raw_event.c.id)
        inserted_ids.update(conn.execute(stmt, chunk).scalars())
    counts["duplicates"] += len(rows) - len(inserted_ids)
    counts["ingested"] += len(inserted_ids)

    links, quarantined = [], []
    for row in sorted((r for r in rows if r["id"] in inserted_ids), key=lambda r: r["ingested_at"]):
        invalid = by_key[(row["source"], row["dedupe_key"])][1]
        if invalid:
            quarantined.append({"raw_event_id": row["id"], "tenant_id": tenant_id, "reason": "invalid_payload",
                                "run_id": run_id, "last_attempt_run_id": run_id})
            continue
        account_id, method = index.resolve(row["source"], row["event_type"], row["payload"])
        if account_id is None:
            quarantined.append({"raw_event_id": row["id"], "tenant_id": tenant_id, "reason": method,
                                "run_id": run_id, "last_attempt_run_id": run_id})
        else:
            # Matched on arrival: the link exists from the moment the record was received.
            links.append({"raw_event_id": row["id"], "tenant_id": tenant_id, "account_id": account_id,
                          "match_method": method, "matched_at": row["ingested_at"]})
    for i in range(0, len(links), BATCH):
        conn.execute(t.event_account.insert(), links[i:i + BATCH])
    for i in range(0, len(quarantined), BATCH):
        conn.execute(t.quarantined_event.insert(), quarantined[i:i + BATCH])
    counts["linked"] += len(links)
    counts["quarantined"] += len(quarantined)
    return counts


def deliver_feed(conn: Connection, tenant_id: UUID, cutoff_at: datetime, run_id: UUID | None,
                 source_timezones: dict) -> Counter:
    """Delivers pending feed records with ingested_at < cutoff_at, oldest first."""
    index = AccountIndex(conn)
    counts: Counter = Counter()
    while True:
        pending = conn.execute(text(
            "SELECT id, source, source_record_id, event_type, occurred_at_raw, ingested_at, payload "
            "FROM sim.pending_feed_event WHERE NOT delivered AND ingested_at < :c "
            "ORDER BY ingested_at, id LIMIT 50000"), {"c": cutoff_at}).mappings().all()
        if not pending:
            return counts
        envelopes = [{"source": p["source"], "source_record_id": p["source_record_id"],
                      "event_type": p["event_type"], "occurred_at": p["occurred_at_raw"],
                      "ingested_at": p["ingested_at"], "payload": p["payload"]} for p in pending]
        counts += ingest_envelopes(conn, tenant_id, envelopes, index, run_id, source_timezones)
        conn.execute(text("UPDATE sim.pending_feed_event SET delivered = true WHERE id = ANY(:ids)"),
                     {"ids": [p["id"] for p in pending]})


def retry_quarantine(conn: Connection, cutoff_at: datetime, run_id: UUID | None) -> Counter:
    """Re-attempts unresolved no_match/ambiguous records (FR-003a). A late match is linked with
    matched_at = this run's cutoff, so earlier cutoffs never see it."""
    index = AccountIndex(conn)
    counts: Counter = Counter()
    rows = conn.execute(text(
        "SELECT q.raw_event_id, r.source, r.event_type, r.payload, r.ingested_at FROM quarantined_event q "
        "JOIN raw_event r ON r.id = q.raw_event_id "
        "WHERE q.resolved_at IS NULL AND q.reason <> 'invalid_payload' AND r.ingested_at < :c"),
        {"c": cutoff_at}).mappings().all()
    resolved = []
    for row in rows:
        account_id, method = index.resolve(row["source"], row["event_type"], row["payload"])
        if account_id is not None:
            conn.execute(t.event_account.insert().values(
                raw_event_id=row["raw_event_id"], tenant_id=_tenant(conn), account_id=account_id,
                match_method=method, matched_at=cutoff_at))
            resolved.append(row["raw_event_id"])
        elif cutoff_at - row["ingested_at"] > AGED_AFTER:
            counts["quarantine_aged"] += 1
    if resolved:
        conn.execute(text("UPDATE quarantined_event SET resolved_at = :c WHERE raw_event_id = ANY(:ids)"),
                     {"c": cutoff_at, "ids": resolved})
    if rows and run_id is not None:
        conn.execute(text("UPDATE quarantined_event SET last_attempt_run_id = :r "
                          "WHERE resolved_at IS NULL AND raw_event_id = ANY(:ids)").bindparams(
            bindparam("ids")), {"r": run_id, "ids": [r["raw_event_id"] for r in rows]})
    counts["quarantine_resolved"] = len(resolved)
    counts["quarantine_open"] = len(rows) - len(resolved)
    return counts


def _tenant(conn: Connection) -> UUID:
    return UUID(conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar())
