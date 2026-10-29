"""Defect injection: the messiness of real feeds (FR-029, contracts/source-events.md).

Adds ingestion times (heavy-tailed lag, late and out-of-order delivery), webhook-retry duplicates,
missing fields, records that match no account, naive local timestamps and clock skew.
"""

import copy
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np

from worklist.synth.config import GeneratorConfig

PRODUCT_TZ = ZoneInfo("America/New_York")
OPTIONAL_FIELDS = {
    "account.upserted": ["owner_email", "segment", "arr"],
    "email.sent": ["is_csm"],
    "email.received": ["is_csm"],
    "ticket.created": ["zendesk_org_id"],
}


def inject(events: list[dict], cfg: GeneratorConfig, rng: np.random.Generator) -> tuple[list[dict], Counter]:
    counts: Counter = Counter()
    n = len(events)

    # Ingestion lag: minutes for webhooks, hours for product rollups; some events days late.
    lag_hours = rng.lognormal(mean=-1.0, sigma=1.2, size=n)
    late = rng.random(n) < cfg.late_share
    late_days = rng.uniform(1, 10, size=n)
    for i, ev in enumerate(events):
        hours = float(lag_hours[i]) + (float(rng.uniform(2, 30)) if ev["source"] == "product" else 0.0)
        if late[i]:
            hours += float(late_days[i]) * 24
            counts["late"] += 1
        ev["ingested"] = ev["at"] + timedelta(hours=hours)

    # Ticket updates delivered before their create (out-of-order webhooks).
    created = {ev["payload"]["ticket_id"]: ev for ev in events if ev["type"] == "ticket.created"}
    for ev in events:
        if ev["type"] == "ticket.updated" and rng.random() < 0.25:
            parent = created.get(ev["payload"]["ticket_id"])
            if parent is not None and parent["ingested"] <= ev["ingested"]:
                parent["ingested"] = ev["ingested"] + timedelta(hours=float(rng.uniform(1, 48)))
                counts["update_before_create"] += 1

    # Missing optional fields.
    for ev in events:
        for name in OPTIONAL_FIELDS.get(ev["type"], []):
            if name in ev["payload"] and rng.random() < cfg.missing_field_rate:
                ev["payload"][name] = None
                counts["missing_field"] += 1

    # Records that match no account (deleted accounts / unknown orgs).
    extra = []
    for i in rng.choice(n, size=int(n * cfg.unmatched_rate / 4), replace=False):
        ev = copy.deepcopy(events[int(i)])
        for key in ("sf_account_id", "stripe_customer_id", "product_org_id", "zendesk_org_id"):
            if key in ev["payload"] and ev["payload"][key]:
                ev["payload"][key] = "deleted_" + str(ev["payload"][key])
        for key in ("from_domain", "to_domain", "domain"):
            if key in ev["payload"] and ev["payload"][key] and not str(ev["payload"][key]).endswith(".demo"):
                ev["payload"][key] = "unknown-" + str(ev["payload"][key])
        if "requester_email" in ev["payload"]:
            ev["payload"]["requester_email"] = "x@unknown-org.example"
        if ev["rid"]:
            ev["rid"] = "orphan_" + ev["rid"]
        ev["acc"] = None
        extra.append(ev)
        counts["orphan_record"] += 1
    events.extend(extra)

    # Clock skew: business time after ingestion time (rejected as invalid_payload on ingest).
    for i in rng.choice(len(events), size=int(len(events) * cfg.clock_skew_rate), replace=False):
        ev = events[int(i)]
        ev["at"] = ev["ingested"] + timedelta(days=float(rng.uniform(2, 5)))
        counts["clock_skew"] += 1

    # Webhook retries: the same record delivered again later.
    dups = []
    for i in rng.choice(len(events), size=int(len(events) * cfg.duplicate_rate), replace=False):
        ev = copy.deepcopy(events[int(i)])
        ev["ingested"] = ev["ingested"] + timedelta(minutes=float(rng.uniform(1, 600)))
        dups.append(ev)
        counts["duplicate"] += 1
    events.extend(dups)

    # Serialise business time: product sends naive local timestamps, others UTC with offset.
    for ev in events:
        if ev["source"] == "product":
            ev["occurred_raw"] = ev["at"].astimezone(PRODUCT_TZ).replace(tzinfo=None).isoformat()
            counts["naive_timestamp"] += 1
        else:
            ev["occurred_raw"] = ev["at"].isoformat()

    events.sort(key=lambda e: e["ingested"])
    return events, counts
