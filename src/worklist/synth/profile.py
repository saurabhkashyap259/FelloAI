"""Data profile report (FR-029): volumes, defects, sparsity, churn and the planted-driver table."""

from collections import Counter, defaultdict
from datetime import timedelta

from worklist.synth.config import DRIVERS, FROZEN_AT
from worklist.synth.generate import Dataset, outcome_rows


def build_profile(ds: Dataset) -> dict:
    cfg = ds.cfg
    by_source = Counter(e["source"] for e in ds.events)
    by_tenant = Counter(cfg.tenants[e["t"]].name for e in ds.events)
    out_of_order = sum(1 for a, b in zip(ds.events, ds.events[1:], strict=False) if b["at"] < a["at"])

    # Sparse: fewer than 5 records in the 180 days before the end of history.
    recent = Counter(e["acc"] for e in ds.events
                     if e["acc"] is not None and e["at"].date() > cfg.history_end - timedelta(days=180))
    alive = [a for a in ds.accounts if a.churn_date is None or a.churn_date > cfg.history_end]
    sparse = sum(1 for a in alive if recent.get(a.idx, 0) < 5)

    outcomes = outcome_rows(ds)
    churned = [o for o in outcomes if o["outcome"] == "churned"]
    term_cycles = [o for o in outcomes if o["acc"] is not None and ds.accounts[o["acc"]].contract_type == "term"]
    silent = [o for o in churned if o["is_silent_churn"]]

    # Planted-driver table: prevalence across observed cycles and churn rate with/without.
    with_d: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    without_d: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    observed = [(a, c) for a in ds.accounts for c in a.cycles if c.outcome not in ("pending",)
                and c.renewal_date >= a.active_from]
    for _acc, cyc in observed:
        for d in DRIVERS:
            bucket = with_d if d.name in cyc.drivers else without_d
            bucket[d.name][0] += 1
            bucket[d.name][1] += cyc.outcome == "churned"
    drivers = []
    for d in DRIVERS:
        n_with, churn_with = with_d[d.name]
        n_without, churn_without = without_d[d.name]
        drivers.append({
            "name": d.name, "definition": d.definition, "log_odds_effect": d.effect,
            "prevalence": round(n_with / max(1, len(observed)), 4),
            "churn_rate_with": round(churn_with / max(1, n_with), 4),
            "churn_rate_without": round(churn_without / max(1, n_without), 4),
            "expected_signals": list(d.signals),
        })

    return {
        "generator": {"seed": cfg.seed, "frozen_at": FROZEN_AT, "history_end": cfg.history_end.isoformat(),
                      "history_days": cfg.history_days, "feed_days": cfg.feed_days,
                      "usage_rollup_period_days": ds.usage_period_days},
        "volumes": {"accounts": len(ds.accounts), "events": len(ds.events), "by_source": dict(by_source),
                    "by_tenant": dict(by_tenant),
                    "accounts_by_tenant": {t.name: t.accounts for t in cfg.tenants}},
        "defects": {**dict(ds.defects), "out_of_order_deliveries": out_of_order,
                    "crm_billing_conflict_accounts": len(ds.conflicts),
                    "unmatched_records": sum(1 for e in ds.events if e["acc"] is None) + ds.defects.get(
                        "free_mail", 0)},
        "sparse": {"alive_accounts": len(alive), "sparse_accounts": sparse,
                   "sparse_share": round(sparse / max(1, len(alive)), 4)},
        "outcomes": {"recorded_renewals": len(outcomes), "churned": len(churned),
                     "term_churn_rate_per_renewal": round(
                         sum(o["outcome"] == "churned" for o in term_cycles) / max(1, len(term_cycles)), 4),
                     "downgraded": sum(o["outcome"] == "downgraded" for o in outcomes),
                     "silent_churn": len(silent),
                     "silent_share_of_churn": round(len(silent) / max(1, len(churned)), 4)},
        "planted_drivers": drivers,
    }
