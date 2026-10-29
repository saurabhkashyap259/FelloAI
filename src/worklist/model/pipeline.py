"""`worklist train` / `retrain` / `evaluate`: build examples, fit, calibrate, evaluate, gate, register.

Runs as the job role. Examples are built tenant by tenant inside tenant-scoped connections (RLS),
then pooled in memory for training (Clarification Q2).
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from worklist.db.engine import job_engine, list_tenants, tenant_scope
from worklist.log import log
from worklist.model import registry
from worklist.model.calibrate import apply_calibrator, fit_calibrator
from worklist.model.dataset import (
    build_tenant_examples,
    history_start,
    training_rows,
    ts,
    windows_for,
)
from worklist.model.evaluate import evaluate, mean_abs_contributions
from worklist.model.train import FittedModel, fit_baseline, fit_lightgbm
from worklist.signals.registry import SIGNAL_DEFS_VERSION

MIN_TENANT_EXAMPLES, MIN_TENANT_CHURNS = 1000, 50


def sim_today() -> date:
    with job_engine().connect() as conn:
        return conn.execute(text('SELECT "current_date" FROM sim_clock WHERE id = 1')).scalar()


def tenant_settings(tenant_id) -> dict:
    with tenant_scope(job_engine(), tenant_id) as conn:
        return dict(conn.execute(text("SELECT * FROM tenant_settings")).mappings().first())


def build_examples(as_of: pd.Timestamp):
    frames, windows = [], None
    for tenant in list_tenants():
        with tenant_scope(job_engine(), tenant["id"]) as conn:
            if windows is None:
                windows = windows_for(as_of, history_start(conn))
            ex = build_tenant_examples(conn, tenant["id"], windows)
        log("train.examples", tenant=tenant["name"], examples=len(ex),
            churn=int(ex["label"].sum()) if len(ex) else 0)
        if len(ex):
            frames.append(ex)
    return pd.concat(frames, ignore_index=True), windows


def calibrate_and_score(model: FittedModel, examples: pd.DataFrame, calibrators: dict | None = None):
    """Fits per-tenant calibrators on validation rows (unless given), returns (probabilities, calibrators)."""
    raw = model.raw_score(examples)
    calibrators = dict(calibrators or {})
    prob = np.zeros(len(examples))
    for tenant, idx in examples.groupby("tenant_id").indices.items():
        if tenant not in calibrators:
            valid = idx[examples["split"].to_numpy()[idx] == "valid"]
            calibrators[tenant] = fit_calibrator(raw[valid], examples["label"].to_numpy()[valid])
        prob[idx] = apply_calibrator(calibrators[tenant], raw[idx])
    return prob, calibrators


def evaluate_model(model: FittedModel, examples: pd.DataFrame, settings: dict, calibrators: dict | None = None):
    prob, calibrators = calibrate_and_score(model, examples, calibrators)
    pool = examples.assign(probability=prob)
    test_mask = (pool["split"] == "test").to_numpy()
    contrib = model.contributions(examples[test_mask])
    k = int(np.median([s["high_band_size"] for s in settings.values()]))
    floor = float(np.median([s["high_band_floor"] for s in settings.values()]))
    report = evaluate(pool, mean_abs_contributions(contrib), k, floor)
    return report, calibrators, pool


def _pooled_metrics(report: dict) -> dict:
    """What may be stored on the shared model_version row: pooled totals only, no per-tenant data."""
    overall = {k: v for k, v in report["overall"].items()}
    return {"overall": overall, "driver_recovery": report.get("driver_recovery"),
            "success_criteria": report.get("success_criteria")}


def train_and_register(as_of_day: date | None, promote: bool, report_path: str | None, run_type: str) -> int:
    as_of_day = as_of_day or sim_today()
    as_of = ts(as_of_day)
    started = datetime.now(UTC)
    log("train.started", as_of=as_of_day.isoformat(), run_type=run_type)
    tenants = list_tenants()
    settings = {t["id"]: tenant_settings(t["id"]) for t in tenants}
    try:
        examples, w = build_examples(as_of)
    except Exception as exc:  # noqa: BLE001 — record the failed attempt, then fail the job
        _record_attempt(tenants, as_of, run_type, "failed", None, f"example building failed: {exc}", started)
        raise
    train_full = training_rows(examples, "full", w)
    valid = examples[examples["split"] == "valid"]
    log("train.split", train=len(train_full), valid=len(valid), test=int((examples["split"] == "test").sum()),
        churn_train=int(train_full["label"].sum()))

    candidates = {"logreg_baseline/full": ("full", fit_baseline(train_full))}
    for window in ("full", "recent_18m"):
        candidates[f"lightgbm/{window}"] = (window, fit_lightgbm(training_rows(examples, window, w), valid))
    results = {}
    for name, (window, model) in candidates.items():
        report, calibrators, _ = evaluate_model(model, examples, settings)
        results[name] = {"window": window, "model": model, "report": report, "calibrators": calibrators}
        log("train.candidate", name=name, **{k: report["overall"][k] for k in
                                             ("top10_hit_rate", "hit_rate_60d", "pr_auc", "brier", "high_band_lift")})

    # Reference for the gate: the current approved global model, re-evaluated on today's test window
    # with its stored calibrators; the baseline when nothing is approved yet (FR-013).
    with job_engine().begin() as conn:
        current = conn.execute(text("SELECT id FROM model_version WHERE status = 'approved' AND scope = 'global'"))\
            .scalar()
    if current is not None:
        current_model, _ = _load_global(current)
        current_cal = _load_calibrators(current, tenants)
        ref_report, _, _ = evaluate_model(current_model, examples, settings, current_cal or None)
        reference, reference_name = ref_report["overall"], f"current model {str(current)[:8]}"
    else:
        reference, reference_name = results["logreg_baseline/full"]["report"]["overall"], "logistic baseline"

    gates = {name: registry.promotion_gate(r["report"]["overall"], reference, reference_name)
             for name, r in results.items() if name.startswith("lightgbm")}
    passing = [n for n, g in gates.items() if g.promote]
    chosen = max(passing, key=lambda n: results[n]["report"]["overall"]["top10_hit_rate"] or 0) if passing else None
    decision = gates[chosen].reason if chosen else "; ".join(f"{n}: {g.reason}" for n, g in gates.items())
    if not promote and chosen:
        decision = f"not promoted (--no-promote); would have promoted {chosen}"

    window_comparison = {n.split("/")[1]: {k: results[n]["report"]["overall"][k]
                                           for k in ("top10_hit_rate", "hit_rate_60d", "pr_auc", "brier")}
                         for n in results if n.startswith("lightgbm")}
    ids = {}
    with job_engine().begin() as conn:
        for name, r in results.items():
            approved = promote and name == chosen
            if name.startswith("logreg"):
                status, reason = "rejected", "baseline reference (never served)"
            elif approved:
                status, reason = "candidate", gates[name].reason
            else:
                status, reason = "rejected", gates[name].reason if name in gates else decision
                if name == chosen:
                    reason = decision
            metrics = {**_pooled_metrics(r["report"]), "history_window_comparison": window_comparison,
                       "baseline": _pooled_metrics(results["logreg_baseline/full"]["report"])["overall"],
                       "gate_reference": reference_name, "chosen": chosen}
            ids[name] = registry.insert_version(
                conn, scope="global", tenant_id=None, model=r["model"], history_window=r["window"],
                windows=w.as_json(), signal_defs_version=SIGNAL_DEFS_VERSION, metrics=metrics, status=status,
                reason=reason)
            if approved:
                registry.approve(conn, ids[name], "global", None)
    for name, r in results.items():  # calibrators + per-tenant evaluation, RLS-protected per tenant
        for tenant in tenants:
            if tenant["id"] in r["calibrators"]:
                with tenant_scope(job_engine(), tenant["id"]) as conn:
                    registry.save_calibrator(conn, tenant["id"], ids[name], r["calibrators"][tenant["id"]],
                                             r["report"]["per_tenant"].get(str(tenant["id"]), {}))

    tenant_decisions = _tenant_models(examples, results, chosen, w, settings, tenants, promote)
    status = "succeeded"
    _record_attempt(tenants, as_of, run_type, status, ids.get(chosen) if chosen and promote else None,
                    decision, started)

    out = Path(report_path or f"reports/eval_{as_of_day.isoformat()}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    names = {str(t["id"]): t["name"] for t in tenants}
    out.write_text(json.dumps({
        "as_of": as_of_day.isoformat(), "windows": w.as_json(), "signal_defs_version": SIGNAL_DEFS_VERSION,
        "decision": decision, "chosen": chosen, "promoted": bool(promote and chosen),
        "gate_reference": reference_name, "history_window_comparison": window_comparison,
        "tenant_models": tenant_decisions, "tenant_names": names,
        "candidates": {n: {**r["report"], "model_version_id": str(ids[n])} for n, r in results.items()},
    }, indent=2, default=str))
    log("train.done", decision=decision, report=str(out))
    return 0


def _tenant_models(examples, results, chosen, w, settings, tenants, promote) -> dict:
    """Tenant-specific LightGBM where the tenant has enough history; adopted only if it passes the gate
    against the pooled model on that tenant's held-out data (FR-012a)."""
    decisions: dict[str, str] = {}
    if not chosen:
        return decisions
    pooled = results[chosen]
    window = pooled["window"]
    for tenant in tenants:
        tid = tenant["id"]
        ex_t = examples[examples["tenant_id"] == tid]
        train_t = training_rows(ex_t, window, w)
        if len(train_t) < MIN_TENANT_EXAMPLES or train_t["label"].sum() < MIN_TENANT_CHURNS:
            decisions[tenant["name"]] = "not trained: below 1,000 examples / 50 churns"
            continue
        model = fit_lightgbm(train_t, ex_t[ex_t["split"] == "valid"])
        report, calibrators, _ = evaluate_model(model, ex_t, {tid: settings[tid]})
        pooled_t = pooled["report"]["per_tenant"].get(str(tid))
        gate = registry.promotion_gate(report["overall"], pooled_t or {}, "pooled model on this tenant")
        with tenant_scope(job_engine(), tid) as conn:
            model_id = registry.insert_version(
                conn, scope="tenant", tenant_id=tid, model=model, history_window=window, windows=w.as_json(),
                signal_defs_version=SIGNAL_DEFS_VERSION, metrics=_pooled_metrics(report),
                status="candidate" if gate.promote and promote else "rejected", reason=gate.reason)
            registry.save_calibrator(conn, tid, model_id, calibrators[tid], report["overall"])
            if gate.promote and promote:
                registry.approve(conn, model_id, "tenant", tid)
        decisions[tenant["name"]] = gate.reason
        log("train.tenant_model", tenant=tenant["name"], decision=gate.reason)
    return decisions


def _load_global(model_id):
    with job_engine().begin() as conn:
        return registry.load_version(conn, model_id)


def _load_calibrators(model_id, tenants) -> dict:
    out = {}
    for tenant in tenants:
        with tenant_scope(job_engine(), tenant["id"]) as conn:
            row = conn.execute(text("SELECT method, params FROM tenant_calibrator WHERE model_version_id = :m"),
                               {"m": model_id}).first()
        if row:
            out[tenant["id"]] = {"method": row.method, "params": row.params["calibrator"]}
    return out


def _record_attempt(tenants, as_of, run_type, status, model_id, reason, started) -> None:
    """One retrain row per tenant per cutoff (FR-014a); a same-day rerun updates it."""
    for tenant in tenants:
        with tenant_scope(job_engine(), tenant["id"]) as conn:
            conn.execute(text(
                "INSERT INTO pipeline_run (id, tenant_id, run_type, cutoff_at, status, model_version_id, started_at, "
                "finished_at, counts, error) VALUES (:id, :t, 'retrain', :c, :s, :m, :st, now(), :counts, :err) "
                "ON CONFLICT (tenant_id, run_type, cutoff_at) DO UPDATE SET status = :s, model_version_id = :m, "
                "started_at = :st, finished_at = now(), counts = :counts, error = :err"),
                {"id": registry.t.new_id(), "t": tenant["id"], "c": as_of.to_pydatetime(), "s": status,
                 "m": model_id, "st": started, "counts": json.dumps({"decision": reason, "trigger": run_type}),
                 "err": None if status == "succeeded" else reason})


def reevaluate(model_version: str) -> int:
    from uuid import UUID

    model_id = UUID(model_version)
    tenants = list_tenants()
    model, _ = _load_global(model_id)
    examples, _ = build_examples(ts(sim_today()))
    settings = {t["id"]: tenant_settings(t["id"]) for t in tenants}
    report, _, _ = evaluate_model(model, examples, settings, _load_calibrators(model_id, tenants) or None)
    out = Path(f"reports/reeval_{model_version[:8]}_{sim_today().isoformat()}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    log("evaluate.done", report=str(out), **{k: report["overall"][k] for k in ("top10_hit_rate", "pr_auc", "brier")})
    return 0
