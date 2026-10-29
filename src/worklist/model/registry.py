"""Model versions, the promotion gate and loading the approved model (FR-012a, FR-013, data-model.md).

Per-tenant calibrators and per-tenant evaluation results live in `tenant_calibrator`, which is
RLS-protected, so one tenant can never read another tenant's metrics (FR-025a). `model_version.metrics`
holds pooled totals only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, text

from worklist.db import tables as t
from worklist.model.train import FittedModel

TOLERANCE = 0.01


@dataclass
class GateResult:
    promote: bool
    reason: str


def promotion_gate(candidate: dict, reference: dict, reference_name: str) -> GateResult:
    """FR-013: no worse than the reference by more than 0.01 on top-10 hit rate, PR-AUC and Brier,
    and strictly better on top-10 hit rate or PR-AUC."""
    c_hit, r_hit = candidate.get("top10_hit_rate") or 0.0, reference.get("top10_hit_rate") or 0.0
    c_pr, r_pr = candidate.get("pr_auc") or 0.0, reference.get("pr_auc") or 0.0
    c_br, r_br = candidate.get("brier") or 1.0, reference.get("brier") or 1.0
    detail = (f"hit {c_hit:.3f} vs {r_hit:.3f}, PR-AUC {c_pr:.3f} vs {r_pr:.3f}, "
              f"Brier {c_br:.4f} vs {r_br:.4f} ({reference_name})")
    worse = []
    if c_hit < r_hit - TOLERANCE:
        worse.append("top-10 hit rate")
    if c_pr < r_pr - TOLERANCE:
        worse.append("PR-AUC")
    if c_br > r_br + TOLERANCE:
        worse.append("Brier")
    if worse:
        return GateResult(False, f"rejected: worse on {', '.join(worse)} by more than {TOLERANCE}; {detail}")
    if not (c_hit > r_hit or c_pr > r_pr):
        return GateResult(False, f"rejected: not strictly better on hit rate or PR-AUC; {detail}")
    return GateResult(True, f"promoted: passes gate; {detail}")


def insert_version(conn: Connection, *, scope: str, tenant_id: UUID | None, model: FittedModel,
                   history_window: str, windows: dict, signal_defs_version: str, metrics: dict,
                   status: str, reason: str) -> UUID:
    model_id = t.new_id()
    conn.execute(t.model_version.insert().values(
        id=model_id, scope=scope, tenant_id=tenant_id, algorithm=model.algorithm, history_window=history_window,
        trained_at=datetime.now(UTC), windows=windows, signal_defs_version=signal_defs_version,
        artifact=model.to_bytes(), metrics=metrics, status=status, decision_reason=reason,
        decided_at=datetime.now(UTC)))
    return model_id


def approve(conn: Connection, model_id: UUID, scope: str, tenant_id: UUID | None) -> None:
    """Retires the previous approved model in the same scope, then approves this one."""
    if scope == "global":
        conn.execute(text("UPDATE model_version SET status = 'retired' "
                          "WHERE status = 'approved' AND scope = 'global' AND id <> :id"), {"id": model_id})
    else:
        conn.execute(text("UPDATE model_version SET status = 'retired' WHERE status = 'approved' "
                          "AND scope = 'tenant' AND tenant_id = :t AND id <> :id"), {"t": tenant_id, "id": model_id})
    conn.execute(text("UPDATE model_version SET status = 'approved' WHERE id = :id"), {"id": model_id})


def save_calibrator(conn: Connection, tenant_id: UUID, model_id: UUID, calibrator: dict, evaluation: dict) -> None:
    conn.execute(text(
        "INSERT INTO tenant_calibrator (tenant_id, model_version_id, method, params) VALUES (:t, :m, :method, :p) "
        "ON CONFLICT (tenant_id, model_version_id) DO UPDATE SET method = :method, params = :p"),
        {"t": tenant_id, "m": model_id, "method": calibrator["method"],
         "p": _json({"calibrator": calibrator["params"], "evaluation": evaluation})})


def _json(value) -> str:
    import json

    return json.dumps(value, default=str)


@dataclass
class ApprovedModel:
    id: UUID
    scope: str
    model: FittedModel
    signal_defs_version: str
    calibrator: dict
    metrics: dict


def load_approved(conn: Connection) -> ApprovedModel | None:
    """For the connection's tenant: its approved tenant-specific model, else the approved global one,
    together with the tenant's calibrator for it. None when nothing usable is approved."""
    rows = conn.execute(text(
        "SELECT m.id, m.scope, m.artifact, m.signal_defs_version, m.metrics, c.method, c.params "
        "FROM model_version m JOIN tenant_calibrator c ON c.model_version_id = m.id "
        "WHERE m.status = 'approved' ORDER BY (m.scope = 'tenant') DESC LIMIT 1")).mappings().first()
    if rows is None:
        return None
    calibrator = {"method": rows["method"], "params": rows["params"]["calibrator"]}
    return ApprovedModel(rows["id"], rows["scope"], FittedModel.from_bytes(rows["artifact"]),
                         rows["signal_defs_version"], calibrator, rows["metrics"])


def load_version(conn: Connection, model_id: UUID) -> tuple[FittedModel, dict]:
    row = conn.execute(text("SELECT artifact, metrics FROM model_version WHERE id = :id"), {"id": model_id}).first()
    return FittedModel.from_bytes(row.artifact), row.metrics
