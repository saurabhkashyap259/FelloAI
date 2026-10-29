"""Planted churn drivers and renewal outcomes (FR-029, FR-030).

Each renewal cycle draws its drivers from the account's latent fragility, then the outcome is a
logistic function of fragility + active drivers, sampled once. A separate "silent churn" archetype
produces churn with only usage and support signals and no CRM, email or billing warnings.
"""

from datetime import date, timedelta

import numpy as np

from worklist.synth.config import DRIVERS, GeneratorConfig
from worklist.synth.world import Account, Cycle, history_start, renewal_dates

ONSET_WINDOWS = {  # days before renewal (term); monthly cycles scale these down
    "usage_decline": (150, 60),
    "seat_underutilisation": (365, 300),
    "ticket_spike_slow_resolution": (140, 60),
    "payment_failures": (130, 60),
    "champion_departure": (160, 65),
    "no_csm_contact": (150, 150),
}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def _onset(rng: np.random.Generator, renewal: date, window: tuple[int, int], monthly: bool) -> date:
    hi, lo = window
    if monthly:
        hi, lo = min(hi, 80), min(lo, 25)
    return renewal - timedelta(days=int(rng.integers(lo, hi + 1)))


def simulate_cycles(acc: Account, cfg: GeneratorConfig, rng: np.random.Generator) -> None:
    hist_start = history_start(cfg)
    monthly = acc.contract_type == "monthly"
    arr = acc.arr
    for start, renewal in renewal_dates(acc, cfg.history_end):
        frag = 0.7 * acc.fragility + 0.7 * float(rng.normal())
        cyc = Cycle(renewal_date=renewal, start=start, fragility=frag, arr_before=arr)
        pending = renewal > cfg.history_end
        observed = renewal >= hist_start
        logit = cfg.base_logit + cfg.fragility_effect * frag - (0.8 if monthly else 0.0)

        if observed and not acc.sparse and rng.random() < cfg.silent_archetype_rate * (1 + max(frag, 0)):
            cyc.silent = True
            chosen = rng.choice(len(("usage_decline", "ticket_spike_slow_resolution", "bad_csat")),
                                size=int(rng.integers(2, 4)), replace=False)
            for c in chosen:
                name = ("usage_decline", "ticket_spike_slow_resolution", "bad_csat")[c]
                if name == "bad_csat":
                    cyc.bad_csat = True
                else:
                    cyc.drivers[name] = _onset(rng, renewal, (110, 45), monthly)
            logit += cfg.silent_effect
        elif observed:
            for d in DRIVERS:
                if rng.random() < _sigmoid(d.base_logit + d.fragility_weight * frag):
                    cyc.drivers[d.name] = _onset(rng, renewal, ONSET_WINDOWS[d.name], monthly)
                    logit += d.effect
        if "usage_decline" in cyc.drivers:
            cyc.decline_depth = float(rng.uniform(0.35, 0.65))

        if pending:
            cyc.outcome = "pending"
        elif not observed:
            cyc.outcome = "renewed"  # survivorship: accounts that churned before history aren't generated
        elif rng.random() < _sigmoid(logit):
            cyc.outcome = "churned"
        elif rng.random() < cfg.downgrade_share_of_renewals:
            cyc.outcome = "downgraded"

        if cyc.outcome == "churned":
            cyc.arr_after = 0.0
            if not cyc.silent:
                if rng.random() < 0.5:
                    cyc.cancel_notice = renewal - timedelta(days=int(rng.integers(15 if monthly else 20, 50)))
                if rng.random() < 0.4 and not monthly:
                    cyc.opp_at_risk = renewal - timedelta(days=int(rng.integers(25, 60)))
        elif cyc.outcome == "downgraded":
            cyc.arr_after = round(arr * float(rng.uniform(0.6, 0.9)), -2)
        else:
            cyc.arr_after = arr
        acc.cycles.append(cyc)
        if cyc.outcome == "churned":
            acc.churn_date = renewal
            return
        arr = cyc.arr_after if cyc.outcome != "pending" else arr


def is_silent_churn(cyc: Cycle) -> bool:
    """Spec Definitions: no CRM/email/billing warning in the 120 days before renewal, ≥2 of the
    usage/support signals. Checked against what was actually planted for the cycle."""
    if cyc.outcome != "churned" or not cyc.silent:
        return False
    warnings = {"payment_failures", "champion_departure"} & set(cyc.drivers)
    signals = sum(["usage_decline" in cyc.drivers, "ticket_spike_slow_resolution" in cyc.drivers, cyc.bad_csat])
    return not warnings and cyc.cancel_notice is None and cyc.opp_at_risk is None and signals >= 2
