"""Evaluation on the held-out later period (FR-012, research R7).

Replays weekly worklists over the test window with the same banding used in production, then
reports per tenant and overall: top-10 hit rate, lead time, high-band lift, calibration, PR-AUC,
Brier, silent-churn recall and planted-driver recovery, plus a pass/fail flag per success criterion.
"""

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from worklist.model.bands import assign_bands
from worklist.signals.registry import FEATURES
from worklist.synth.config import DRIVERS

MIN_CHURNS_PER_TENANT = 30
MIN_BIN = 50


def replay(pool: pd.DataFrame, k: int, floor: float) -> pd.DataFrame:
    """Adds band/rank per (tenant, cutoff, owner) to every example in the pool."""
    out = []
    for _, group in pool.groupby(["tenant_id", "cutoff"], sort=False):
        band, rank = assign_bands(group["probability"], group["owner_user_id"], k, floor)
        out.append(group.assign(band=band, rank=rank))
    return pd.concat(out) if out else pool.assign(band=[], rank=[])


def calibration_bins(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    if len(p) < bins:
        return []
    order = np.argsort(p)
    result = []
    for chunk in np.array_split(order, bins):
        result.append({"n": int(len(chunk)), "mean_predicted": round(float(p[chunk].mean()), 4),
                       "observed_rate": round(float(y[chunk].mean()), 4)})
    return result


def _churner_leads(replayed: pd.DataFrame) -> pd.DataFrame:
    churn = replayed[(replayed["split"] == "test") & (replayed["label"] == 1)]
    rows = []
    for (tenant, account, renewal), g in churn.groupby(["tenant_id", "account_id", "renewal_date"]):
        hits = g[g["band"] == "high"]
        lead = int((renewal - hits["cutoff"].min()).days) if len(hits) else None
        rows.append({"tenant_id": tenant, "account_id": account, "renewal_date": renewal, "lead_days": lead,
                     "silent": bool(g["is_silent_churn"].any())})
    return pd.DataFrame(rows, columns=["tenant_id", "account_id", "renewal_date", "lead_days", "silent"])


def _metrics(test: pd.DataFrame, leads: pd.DataFrame) -> dict:
    y, p = test["label"].to_numpy(), test["probability"].to_numpy()
    n_churn = int(len(leads))
    hit_any = leads["lead_days"].notna()
    hit_60 = leads["lead_days"].fillna(-1) >= 60
    silent = leads[leads["silent"]]
    high = test["band"] == "high"
    base_rate = float(y.mean()) if len(y) else 0.0
    high_rate = float(test.loc[high, "label"].mean()) if high.any() else 0.0
    bins = calibration_bins(p, y)
    big_bins = [b for b in bins if b["n"] >= MIN_BIN]
    return {
        "examples": int(len(test)), "positive_examples": int(y.sum()), "churned_accounts": n_churn,
        "pr_auc": round(float(average_precision_score(y, p)), 4) if 0 < y.sum() < len(y) else None,
        "roc_auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None,
        "brier": round(float(brier_score_loss(y, p)), 4) if len(y) else None,
        "top10_hit_rate": round(float(hit_any.mean()), 4) if n_churn else None,
        "hit_rate_60d": round(float(hit_60.mean()), 4) if n_churn else None,
        "median_lead_days": float(leads.loc[hit_any, "lead_days"].median()) if hit_any.any() else None,
        "high_band_churn_rate": round(high_rate, 4), "base_churn_rate": round(base_rate, 4),
        "high_band_lift": round(high_rate / base_rate, 2) if base_rate else None,
        "calibration_bins": bins,
        "calibration_max_gap": round(max((abs(b["mean_predicted"] - b["observed_rate"]) for b in big_bins),
                                         default=0.0), 4),
        "silent_churn": {
            "count": int(len(silent)),
            "recall_30d": round(float((silent["lead_days"].fillna(-1) >= 30).mean()), 4) if len(silent) else None,
            "lead_days": [None if pd.isna(v) else int(v) for v in silent["lead_days"]],
        },
    }


def driver_recovery(mean_abs: pd.Series) -> dict:
    top10 = list(mean_abs.sort_values(ascending=False).index[:10])
    recovered = {d.name: bool(set(d.signals) & set(top10)) for d in DRIVERS}
    return {"top10_signals": top10, "drivers": recovered, "recovered_count": sum(recovered.values()),
            "mean_abs_contribution": {k: round(float(v), 4) for k, v in mean_abs.sort_values(ascending=False).items()}}


def evaluate(pool: pd.DataFrame, mean_abs_contrib: pd.Series | None, k: int = 10, floor: float = 0.15) -> dict:
    """`pool`: examples with calibrated `probability` (all splits at test-window cutoffs)."""
    test_start_cut = pool.loc[pool["split"] == "test", "renewal_date"].min() - pd.Timedelta(days=90)
    replayed = replay(pool[pool["cutoff"] >= test_start_cut], k, floor)
    test = replayed[replayed["split"] == "test"]
    leads = _churner_leads(replayed)
    report: dict = {"overall": _metrics(test, leads), "per_tenant": {}}
    for tenant, g in test.groupby("tenant_id"):
        report["per_tenant"][str(tenant)] = _metrics(g, leads[leads["tenant_id"] == tenant])
    if mean_abs_contrib is not None:
        report["driver_recovery"] = driver_recovery(mean_abs_contrib)
    report["success_criteria"] = success_flags(report)
    report["churner_leads"] = [{**r, "tenant_id": str(r["tenant_id"]), "account_id": str(r["account_id"]),
                                "renewal_date": r["renewal_date"].date().isoformat()}
                               for r in leads.to_dict("records")]
    return report


def success_flags(report: dict) -> dict:
    o = report["overall"]
    eligible = [m for m in report["per_tenant"].values() if m["churned_accounts"] >= MIN_CHURNS_PER_TENANT]

    def all_ok(fn) -> bool:
        return bool(fn(o)) and all(fn(m) for m in eligible)

    flags = {
        "SC-001_hit_rate_60d_ge_0.70": all_ok(lambda m: (m["hit_rate_60d"] or 0) >= 0.70),
        "SC-002_high_band_lift_ge_3": all_ok(lambda m: (m["high_band_lift"] or 0) >= 3),
        "SC-004_calibration_gap_le_0.10": all_ok(lambda m: m["calibration_max_gap"] <= 0.10),
        "SC-012_silent_recall_30d_ge_0.70": (o["silent_churn"]["recall_30d"] or 0) >= 0.70,
    }
    if "driver_recovery" in report:
        flags["SC-011_drivers_recovered_ge_5"] = report["driver_recovery"]["recovered_count"] >= 5
    return flags


def mean_abs_contributions(contrib: np.ndarray) -> pd.Series:
    return pd.Series(np.abs(contrib[:, :len(FEATURES)]).mean(axis=0), index=FEATURES)
