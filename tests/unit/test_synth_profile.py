"""The default dataset has the volumes and messiness the spec requires (US7 scenarios 1–4, FR-029/030)."""

import pytest

from worklist.synth.generate import generate, scaled_config
from worklist.synth.profile import build_profile

CSM_COUNTS = [22, 12, 7, 2]


@pytest.fixture(scope="module")
def profile():
    csms = [[f"csm{i:02d}@t{t}.demo" for i in range(n)] for t, n in enumerate(CSM_COUNTS)]
    ds = generate(scaled_config(), csms, [f"t{t}.demo" for t in range(4)])
    return build_profile(ds)


def test_volumes_within_ten_percent(profile):
    assert profile["volumes"]["accounts"] == 5000
    assert 180_000 <= profile["volumes"]["events"] <= 220_000
    assert set(profile["volumes"]["by_source"]) == {"crm", "email", "support", "product", "billing"}
    assert len(profile["volumes"]["by_tenant"]) == 4


def test_defects_present(profile):
    d = profile["defects"]
    for key in ("duplicate", "missing_field", "late", "out_of_order_deliveries", "naive_timestamp",
                "unmatched_records", "crm_billing_conflict_accounts", "update_before_create", "clock_skew"):
        assert d[key] > 0, key


def test_sparse_and_silent_shares(profile):
    assert 0.08 <= profile["sparse"]["sparse_share"] <= 0.12
    assert 0.12 <= profile["outcomes"]["silent_share_of_churn"] <= 0.18


def test_planted_driver_table(profile):
    drivers = profile["planted_drivers"]
    assert len(drivers) == 6
    for d in drivers:
        assert d["definition"] and d["prevalence"] > 0
        assert d["churn_rate_with"] > d["churn_rate_without"], d["name"]
