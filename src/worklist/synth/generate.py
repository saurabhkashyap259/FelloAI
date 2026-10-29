"""Builds the whole synthetic dataset in memory (deterministic for a given config + seed)."""

import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import timedelta

import numpy as np

from worklist.synth.config import DEFAULT, GeneratorConfig, TenantSpec
from worklist.synth.defects import inject
from worklist.synth.drivers import is_silent_churn, simulate_cycles
from worklist.synth.emit import Emitter
from worklist.synth.world import Account, build_accounts


@dataclass
class Dataset:
    cfg: GeneratorConfig
    accounts: list[Account]
    events: list[dict]
    defects: Counter
    usage_period_days: int
    conflicts: dict[int, dict]


def scaled_config(seed: int = 42, accounts: int = 5000, events: int = 200_000, history_days: int = 1095,
                  feed_days: int = 30, base: GeneratorConfig = DEFAULT) -> GeneratorConfig:
    """Scales tenant sizes proportionally when --accounts differs from the frozen default."""
    total = sum(t.accounts for t in base.tenants)
    tenants = tuple(TenantSpec(t.name, max(10, round(t.accounts * accounts / total))) for t in base.tenants)
    return replace(base, seed=seed, tenants=tenants, target_events=events, history_days=history_days,
                   feed_days=feed_days)


def generate(cfg: GeneratorConfig, csm_emails: list[list[str]], vendor_domains: list[str]) -> Dataset:
    rng = np.random.Generator(np.random.PCG64(cfg.seed))
    accounts = build_accounts(cfg, rng, [len(c) for c in csm_emails])
    for acc in accounts:
        simulate_cycles(acc, cfg, rng)

    conflicts: dict[int, dict] = {}
    for acc in accounts:
        if rng.random() < cfg.conflict_share:
            kind = rng.choice(["renewal", "arr", "both"])
            c: dict = {}
            if kind in ("renewal", "both"):
                c["renewal_offset"] = int(rng.choice([-1, 1]) * rng.integers(10, 41))
            if kind in ("arr", "both"):
                c["stale_arr"] = round(acc.arr * float(rng.uniform(0.7, 0.9)), -2)
            conflicts[acc.idx] = c

    emitter = Emitter(cfg, rng, csm_emails, vendor_domains)
    for acc in accounts:
        emitter.crm(acc, conflicts.get(acc.idx, {}))
        emitter.billing(acc)
        emitter.support(acc)
        emitter.email(acc)

    # Product usage gets the remaining event budget: the rollup period follows from it.
    overhead = 1 + cfg.duplicate_rate + cfg.unmatched_rate / 4
    remaining = cfg.target_events / overhead - len(emitter.events) - len(accounts)
    active_days = sum((emitter.end(a) - a.active_from).days for a in accounts if not a.sparse)
    period = int(min(35, max(7, math.ceil(active_days / max(remaining, 1) * 0.94))))
    for acc in accounts:
        emitter.product(acc, period)

    events, defects = inject(emitter.events, cfg, rng)
    return Dataset(cfg, accounts, events, defects, period, conflicts)


def demo_start(cfg: GeneratorConfig):
    return cfg.history_end - timedelta(days=cfg.feed_days)


def outcome_rows(ds: Dataset) -> list[dict]:
    """Ground-truth renewal outcomes, available from renewal date + grace (term 90d, monthly 30d)."""
    rows = []
    for acc in ds.accounts:
        grace = 90 if acc.contract_type == "term" else 30
        for cyc in acc.cycles:
            if cyc.outcome == "pending" or cyc.renewal_date < acc.active_from:
                continue
            if acc.contract_type == "monthly" and cyc.outcome != "churned":
                continue  # monthly plans: only cancellations are recorded outcomes
            rows.append({"acc": acc.idx, "renewal_date": cyc.renewal_date, "outcome": cyc.outcome,
                         "arr_before": cyc.arr_before, "arr_after": cyc.arr_after, "grace_days": grace,
                         "is_silent_churn": is_silent_churn(cyc)})
    return rows
