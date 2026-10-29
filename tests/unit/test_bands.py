"""Per-CSM bands (FR-011) and states (FR-010)."""

import pandas as pd

from worklist.model.bands import assign_bands, assign_confidence, assign_states


def _book(probs, owner="u1"):
    return pd.Series(probs), pd.Series([owner] * len(probs))


def test_high_is_top_k_and_above_floor():
    prob, owner = _book([0.9, 0.5, 0.3, 0.2, 0.16, 0.14, 0.1, 0.06, 0.04])
    band, rank = assign_bands(prob, owner, k=3, floor=0.15)
    assert list(band) == ["high", "high", "high", "medium", "medium", "medium", "medium", "medium", "low"]
    assert list(rank) == list(range(1, 10))


def test_healthy_book_has_fewer_than_k_high():
    prob, owner = _book([0.2, 0.1, 0.08, 0.02])
    band, _ = assign_bands(prob, owner, k=10, floor=0.15)
    assert list(band) == ["high", "medium", "medium", "low"]


def test_overflow_above_floor_goes_to_medium_even_beyond_3k():
    prob, owner = _book([0.9] * 5 + [0.5] * 5)
    band, _ = assign_bands(prob, owner, k=2, floor=0.15)
    assert (band == "high").sum() == 2 and (band == "medium").sum() == 8


def test_bands_are_per_owner():
    prob = pd.Series([0.9, 0.8, 0.2, 0.19])
    owner = pd.Series(["a", "a", "b", "b"])
    band, _ = assign_bands(prob, owner, k=1, floor=0.15)
    assert list(band) == ["high", "medium", "high", "medium"]


def test_state_precedence_and_confidence():
    idx = ["overdue", "later", "sparse", "ok"]
    status = pd.Series(["renewal_overdue", "active", "active", "active"], index=idx)
    days = pd.Series([-5, 200, 30, 30], index=idx)
    insufficient = pd.Series([False, True, True, False], index=idx)
    state = assign_states(status, days, insufficient, has_model=True)
    assert list(state) == ["renewal_overdue", "not_due", "insufficient_data", "scored"]
    assert assign_states(status, days, insufficient, has_model=False)["ok"] == "no_model"
    conf = assign_confidence(state, pd.Series([False, False, False, True], index=idx))
    assert list(conf) == ["normal", "normal", "insufficient_data", "low"]
