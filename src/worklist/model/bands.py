"""Account states, confidence and per-CSM risk bands (FR-010, FR-011, research R8).

Shared by evaluation (worklist replay) and nightly scoring, so the bands measured offline are
exactly the bands CSMs see.
"""

import numpy as np
import pandas as pd

DEFAULT_K, DEFAULT_FLOOR, MEDIUM_MIN = 10, 0.15, 0.05


def assign_states(status: pd.Series, days_to_renewal: pd.Series, insufficient: pd.Series,
                  has_model: bool) -> pd.Series:
    """renewal_overdue → not_due → insufficient_data → no_model → scored (first match wins)."""
    state = pd.Series("scored" if has_model else "no_model", index=status.index, dtype=object)
    state[insufficient.reindex(status.index).fillna(True).astype(bool)] = "insufficient_data"
    state[days_to_renewal > 90] = "not_due"
    state[status.isin(["renewal_overdue", "outcome_unknown"])] = "renewal_overdue"
    return state


def assign_confidence(state: pd.Series, low_confidence: pd.Series) -> pd.Series:
    conf = pd.Series("normal", index=state.index, dtype=object)
    conf[low_confidence.reindex(state.index).fillna(False).astype(bool)] = "low"
    conf[state == "insufficient_data"] = "insufficient_data"
    return conf


def assign_bands(probability: pd.Series, owner: pd.Series, k: int = DEFAULT_K,
                 floor: float = DEFAULT_FLOOR) -> tuple[pd.Series, pd.Series]:
    """Per owner: high = top K by probability AND ≥ floor; medium = not high, ≥ 5%, and (rank ≤ 3K or
    ≥ floor); low otherwise. Unassigned accounts are ranked together as one book.
    Returns (band, rank_in_owner)."""
    owner_key = owner.astype(object).where(owner.notna(), "__unassigned__")
    rank = probability.groupby(owner_key).rank(method="first", ascending=False).astype(int)
    high = (rank <= k) & (probability >= floor)
    medium = ~high & (probability >= MEDIUM_MIN) & ((rank <= 3 * k) | (probability >= floor))
    band = pd.Series(np.select([high, medium], ["high", "medium"], "low"), index=probability.index)
    return band, rank
