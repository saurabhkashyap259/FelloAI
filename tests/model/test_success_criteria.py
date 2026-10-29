"""Model success criteria on the default generated data (slow; `pytest -m model`).

Per FR-029a the generator is frozen: a failure here is reported as-is (the report is printed),
never fixed by retuning the generator against these results.
"""

import json
from pathlib import Path

import pytest

from worklist.cli import EXIT_OK
from worklist.model.pipeline import train_and_register
from worklist.seed import seed_demo
from worklist.synth.writer import generate_and_load

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("reports")
    assert seed_demo() == EXIT_OK
    assert generate_and_load(42, 5000, 200_000, 1095, 30, str(out / "profile.json"), reset=True) == EXIT_OK
    assert train_and_register(None, True, str(out / "eval.json"), "manual") == EXIT_OK
    return json.loads(Path(out / "eval.json").read_text())


@pytest.fixture(autouse=True)
def clean_db():  # keep the module's data between tests
    yield


def _chosen(report):
    return report["candidates"][report["chosen"]]


def test_model_beats_baseline(report):  # SC-003
    chosen, base = _chosen(report)["overall"], report["candidates"]["logreg_baseline/full"]["overall"]
    assert chosen["top10_hit_rate"] >= base["top10_hit_rate"]
    assert (chosen["median_lead_days"] or 0) >= (base["median_lead_days"] or 0)


@pytest.mark.parametrize("flag", ["SC-001_hit_rate_60d_ge_0.70", "SC-002_high_band_lift_ge_3",
                                  "SC-004_calibration_gap_le_0.10", "SC-011_drivers_recovered_ge_5",
                                  "SC-012_silent_recall_30d_ge_0.70"])
def test_success_criterion(report, flag):
    chosen = _chosen(report)
    if not chosen["success_criteria"][flag]:
        print(json.dumps({k: chosen["overall"][k] for k in ("top10_hit_rate", "hit_rate_60d", "high_band_lift",
                                                             "calibration_max_gap")}, indent=2))
    assert chosen["success_criteria"][flag], f"{flag} not met; reported as-is per FR-029a"
