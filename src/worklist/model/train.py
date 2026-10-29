"""Model fitting: logistic-regression baseline and LightGBM (research R6)."""

import pickle
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from worklist.signals.registry import BY_NAME, FEATURES

LGB_PARAMS = {
    "objective": "binary", "learning_rate": 0.04, "num_leaves": 15, "min_data_in_leaf": 60,
    "feature_fraction": 0.8, "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0,
    "verbose": -1, "seed": 7, "deterministic": True, "num_threads": 4,
}


@dataclass
class FittedModel:
    algorithm: str  # lightgbm | logreg_baseline
    model: Any  # lightgbm.Booster or a scikit-learn Pipeline
    features: list[str] = field(default_factory=lambda: list(FEATURES))

    def raw_score(self, X: pd.DataFrame) -> np.ndarray:
        """Uncalibrated log-odds."""
        if self.algorithm == "lightgbm":
            return self.model.predict(X[self.features], raw_score=True)
        return self.model.decision_function(X[self.features])

    def contributions(self, X: pd.DataFrame) -> np.ndarray:
        """Per-feature log-odds contributions (last column = bias); rows sum to raw_score."""
        if self.algorithm == "lightgbm":
            return self.model.predict(X[self.features], pred_contrib=True)
        imputer, scaler, lr = (self.model.named_steps[k] for k in ("simpleimputer", "standardscaler",
                                                                   "logisticregression"))
        z = scaler.transform(imputer.transform(X[self.features]))
        contrib = z * lr.coef_[0]
        return np.column_stack([contrib, np.full(len(X), lr.intercept_[0])])

    def to_bytes(self) -> bytes:
        return pickle.dumps(self)

    @staticmethod
    def from_bytes(blob: bytes) -> "FittedModel":
        return pickle.loads(blob)  # noqa: S301 — artifacts are written only by our own training job


def fit_baseline(train: pd.DataFrame) -> FittedModel:
    pipe = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                         LogisticRegression(C=0.5, max_iter=2000))
    pipe.fit(train[FEATURES].astype(float), train["label"])
    return FittedModel("logreg_baseline", pipe)


def fit_lightgbm(train: pd.DataFrame, valid: pd.DataFrame) -> FittedModel:
    monotone = [BY_NAME[f].monotone for f in FEATURES]
    dtrain = lgb.Dataset(train[FEATURES].astype(float), train["label"], free_raw_data=False)
    dvalid = lgb.Dataset(valid[FEATURES].astype(float), valid["label"], reference=dtrain)
    booster = lgb.train({**LGB_PARAMS, "monotone_constraints": monotone}, dtrain, num_boost_round=600,
                        valid_sets=[dvalid], callbacks=[lgb.early_stopping(50, verbose=False)])
    return FittedModel("lightgbm", booster)
