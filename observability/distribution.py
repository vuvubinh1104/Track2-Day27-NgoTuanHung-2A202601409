"""Distribution-shift detectors.

Starter used mean ratio only, which misses same-mean shape changes.
The hybrid detector combines mean/median ratio, KS, and PSI.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _ks_statistic(cur: np.ndarray, base: np.ndarray) -> float:
    data_all = np.sort(np.concatenate([cur, base]))
    cdf_cur = np.searchsorted(np.sort(cur), data_all, side="right") / max(cur.size, 1)
    cdf_base = np.searchsorted(np.sort(base), data_all, side="right") / max(base.size, 1)
    return float(np.max(np.abs(cdf_cur - cdf_base)))


def _psi(cur: np.ndarray, base: np.ndarray, bins: int = 10) -> float:
    quantiles = np.linspace(0.0, 1.0, bins + 1)
    breaks = np.unique(np.quantile(base, quantiles))
    if breaks.size < 3:
        return 0.0
    base_pct = np.histogram(base, bins=breaks)[0].astype(float)
    cur_pct = np.histogram(cur, bins=breaks)[0].astype(float)
    base_pct = np.clip(base_pct / max(base.size, 1), 1e-4, None)
    cur_pct = np.clip(cur_pct / max(cur.size, 1), 1e-4, None)
    return float(np.sum((cur_pct - base_pct) * np.log(cur_pct / base_pct)))


def _ratio(current: float, baseline: float) -> float:
    if baseline == 0:
        return float("inf") if current != 0 else 1.0
    if current == 0:
        return float("inf")
    return max(abs(current / baseline), abs(baseline / current))


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
) -> dict[str, Any]:
    cur = np.asarray(list(current_values), dtype=float)
    base = np.asarray(list(baseline_values), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "hybrid:empty", "reason": "empty_input"}

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))
    cur_median = float(np.median(cur))
    base_median = float(np.median(base))
    mean_ratio = _ratio(cur_mean, base_mean)
    median_ratio = _ratio(cur_median, base_median)
    ks = _ks_statistic(cur, base)
    psi = _psi(cur, base)

    mean_hit = mean_ratio >= ratio_threshold
    median_hit = median_ratio >= ratio_threshold
    ks_hit = ks >= 0.45 and cur.size >= 4 and base.size >= 4
    psi_hit = psi >= 0.25

    is_anomaly = bool(mean_hit or median_hit or ks_hit or psi_hit)
    score = float(max(mean_ratio, median_ratio, ks * ratio_threshold, psi * 4.0))
    return {
        "is_anomaly": is_anomaly,
        "score": score,
        "method": "hybrid:ks+psi+mean_ratio",
        "reason": (
            f"baseline_mean={base_mean:.3f}, current_mean={cur_mean:.3f}, "
            f"mean_ratio={mean_ratio:.3f}, median_ratio={median_ratio:.3f}, "
            f"ks={ks:.3f}, psi={psi:.3f}"
        ),
        "ks": ks,
        "psi": psi,
        "mean_ratio": mean_ratio,
        "median_ratio": median_ratio,
    }
