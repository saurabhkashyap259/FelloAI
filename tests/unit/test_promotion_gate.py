"""Promotion gate (FR-013)."""

from worklist.model.registry import promotion_gate

REF = {"top10_hit_rate": 0.60, "pr_auc": 0.40, "brier": 0.060}


def test_better_hit_rate_and_not_worse_elsewhere_promotes():
    assert promotion_gate({"top10_hit_rate": 0.65, "pr_auc": 0.395, "brier": 0.065}, REF, "current").promote


def test_identical_metrics_keep_current():
    result = promotion_gate(dict(REF), REF, "current")
    assert not result.promote and "not strictly better" in result.reason


def test_worse_by_more_than_tolerance_on_any_metric_rejects():
    for worse in ({"pr_auc": 0.38}, {"brier": 0.075}, {"top10_hit_rate": 0.58}):
        candidate = {"top10_hit_rate": 0.70, "pr_auc": 0.50, "brier": 0.05, **worse}
        result = promotion_gate(candidate, REF, "current")
        assert not result.promote and "rejected: worse" in result.reason


def test_reason_names_the_reference():
    assert "logistic baseline" in promotion_gate({"top10_hit_rate": 0.7, "pr_auc": 0.5, "brier": 0.05},
                                                 REF, "logistic baseline").reason
