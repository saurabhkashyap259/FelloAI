"""Seven unattended nights on the default dataset (slow; `pytest -m model`): every night succeeds within
30 minutes, and CSMs get ≤5 alerts per week on average (SC-007, SC-009)."""

import time

import pytest
from sqlalchemy import text

from worklist.agent import pipeline
from worklist.cli import EXIT_OK
from worklist.db.engine import operator_conn
from worklist.model.pipeline import train_and_register
from worklist.seed import seed_demo
from worklist.synth.writer import generate_and_load

pytestmark = pytest.mark.model


@pytest.fixture(autouse=True)
def clean_db():  # this module manages its own data
    yield


def test_seven_nights(tmp_path):
    with operator_conn() as conn:
        loaded = conn.execute(text("SELECT count(*) FROM account")).scalar()
    if not loaded:
        assert seed_demo() == EXIT_OK
        assert generate_and_load(42, 5000, 200_000, 1095, 30, str(tmp_path / "p.json")) == EXIT_OK
        assert train_and_register(None, True, str(tmp_path / "e.json"), "manual") == EXIT_OK
    durations = []
    for _ in range(7):
        started = time.monotonic()
        assert pipeline.nightly(use_llm=False) == pipeline.EXIT_OK
        durations.append(time.monotonic() - started)
    assert max(durations) < 30 * 60
    with operator_conn() as conn:
        csms = conn.execute(text("SELECT count(*) FROM app_user WHERE role = 'csm'")).scalar()
        alerts = conn.execute(text("SELECT count(*) FROM alert a JOIN pipeline_run r ON r.id = a.run_id "
                                   "WHERE r.run_type = 'nightly'")).scalar()
    print(f"nightly durations (s): {[round(d, 1) for d in durations]}; alerts per CSM-week: {alerts / csms:.2f}")
    assert alerts / csms <= 5
