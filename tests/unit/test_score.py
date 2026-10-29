"""Scoring: contributions, reasons, versions and non-scored states (FR-008, FR-009, FR-020)."""

import numpy as np
import pandas as pd
from sqlalchemy import text

from tests.helpers import build_world, score_world, tiny_model
from worklist.db.engine import job_engine, operator_conn, tenant_scope
from worklist.model.score import build_reasons
from worklist.signals.registry import FEATURES


def test_contributions_sum_to_raw_score():
    model = tiny_model()
    X = pd.DataFrame(np.random.default_rng(1).normal(size=(5, len(FEATURES))), columns=FEATURES)
    assert np.allclose(model.contributions(X).sum(axis=1), model.raw_score(X))


def test_reasons_are_top_three_risk_increasing_non_attribute_signals():
    contrib = np.zeros(len(FEATURES) + 1)
    for name, value in (("payment_failures_90d", 1.2), ("days_to_renewal", 2.0), ("tickets_30d", 0.5),
                        ("bounces_90d", 0.3), ("bad_csat_90d", 0.1), ("active_user_ratio", -0.9)):
        contrib[FEATURES.index(name)] = value
    values = pd.Series({f: 1.0 for f in FEATURES})
    contributions, reasons = build_reasons(contrib, values)
    assert contributions[0]["signal"] == "days_to_renewal"  # kept in the full list
    assert [r["signal"] for r in reasons] == ["payment_failures_90d", "tickets_30d", "bounces_90d"]
    assert reasons[0]["text"] == "1 failed payment attempts in the last 90 days"


def test_scores_record_versions_and_out_of_horizon_is_not_scored():
    world = build_world()
    from tests.helpers import approve_tiny_model

    approve_tiny_model([world["tenant"]])
    score_world(world)
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        rows = conn.execute(text("SELECT state, probability, model_version_id, signal_defs_version, days_to_renewal "
                                 "FROM account_score")).mappings().all()
    assert rows and all(r["signal_defs_version"] for r in rows)
    for r in rows:
        if r["days_to_renewal"] > 90:
            assert r["state"] == "not_due" and r["probability"] is None
        if r["state"] == "scored":
            assert r["probability"] is not None and r["model_version_id"] is not None


def test_signal_defs_mismatch_means_no_model():
    world = build_world()
    from tests.helpers import approve_tiny_model

    approve_tiny_model([world["tenant"]])
    with operator_conn() as conn:
        conn.execute(text("UPDATE model_version SET signal_defs_version = 'sd-old'"))
    score_world(world)
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        states = set(conn.execute(text("SELECT state FROM account_score")).scalars())
    assert "scored" not in states and "no_model" in states
