"""Per-tenant calibration of the pooled model (Clarification Q2, research R6).

Isotonic regression on the tenant's own validation predictions, or Platt scaling when the tenant
has fewer than 200 labelled validation examples. Fitted only on that tenant's data (FR-025a).
"""

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

MIN_ISOTONIC = 200


def fit_calibrator(raw_scores: np.ndarray, labels: np.ndarray) -> dict:
    if len(raw_scores) >= MIN_ISOTONIC and labels.sum() >= 10:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
        iso.fit(raw_scores, labels)
        return {"method": "isotonic", "params": {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()}}
    if len(raw_scores) >= 20 and 0 < labels.sum() < len(labels):
        lr = LogisticRegression(C=10.0).fit(raw_scores.reshape(-1, 1), labels)
        return {"method": "platt", "params": {"a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0])}}
    return {"method": "platt", "params": {"a": 1.0, "b": 0.0}}  # identity on log-odds


def apply_calibrator(calibrator: dict, raw_scores: np.ndarray) -> np.ndarray:
    raw_scores = np.asarray(raw_scores, dtype=float)
    p = calibrator["params"]
    if calibrator["method"] == "isotonic":
        return np.clip(np.interp(raw_scores, p["x"], p["y"]), 0.001, 0.999)
    return 1.0 / (1.0 + np.exp(-(p["a"] * raw_scores + p["b"])))
