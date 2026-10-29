"""Same seed → identical world, events and outcomes (US7 scenario 1)."""

import hashlib
import json

from worklist.synth.generate import generate, outcome_rows, scaled_config

CSMS = [["a@x.demo", "b@x.demo"], ["c@y.demo"]]


def _digest(seed: int) -> str:
    cfg = scaled_config(seed=seed, accounts=120, events=4000, history_days=400, feed_days=10)
    cfg = cfg.__class__(**{**cfg.__dict__, "tenants": cfg.tenants[:2]})
    ds = generate(cfg, CSMS, ["x.demo", "y.demo"])
    blob = json.dumps({
        "accounts": [(a.name, a.domain, a.contract_type, str(a.contract_start), a.seats) for a in ds.accounts],
        "events": [(e["source"], e["rid"], e["type"], e["occurred_raw"], e["ingested"].isoformat(), e["payload"])
                   for e in ds.events],
        "outcomes": [(o["acc"], str(o["renewal_date"]), o["outcome"]) for o in outcome_rows(ds)],
    }, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def test_same_seed_same_data():
    assert _digest(7) == _digest(7)


def test_different_seed_different_data():
    assert _digest(7) != _digest(8)
