"""Anomaly detectors.

`zscore` remains the simple baseline. `auto` is context-aware: same-weekday /
same-segment history, MAD, EWMA, and relative volume drops.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _as_float_array(history: Iterable[float]) -> np.ndarray:
    return np.asarray(list(history), dtype=float)


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = _as_float_array(history)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Modified z-score using median/MAD. Zero-MAD means a constant baseline."""
    values = _as_float_array(history)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "insufficient_history"}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    current_f = float(current)
    if mad == 0:
        # Constant history: any material deviation is an anomaly.
        delta = abs(current_f - median)
        relative = delta / abs(median) if median != 0 else delta
        is_anomaly = delta > 0 and relative >= 0.05
        score = float("inf") if is_anomaly else 0.0
        return {
            "is_anomaly": bool(is_anomaly),
            "score": score,
            "method": "mad",
            "reason": f"median={median:.3f}, mad=0, relative_delta={relative:.3f}",
        }
    modified_z = 0.6745 * abs(current_f - median) / mad
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": f"median={median:.3f}, mad={mad:.3f}, threshold={threshold}",
    }


def ewma_detector(
    current: float,
    history: Iterable[float],
    *,
    alpha: float = 0.3,
    threshold: float = 3.0,
) -> dict[str, Any]:
    values = _as_float_array(history)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "ewma", "reason": "insufficient_history"}
    mean = float(values[0])
    var = 0.0
    for x in values[1:]:
        delta = float(x) - mean
        mean = alpha * float(x) + (1.0 - alpha) * mean
        var = (1.0 - alpha) * (var + alpha * delta * delta)
    std = float(np.sqrt(max(var, 0.0)))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "ewma",
        "reason": f"ewma={mean:.3f}, ewma_std={std:.3f}, alpha={alpha}, threshold={threshold}",
    }


def _extract_same_segment(values: np.ndarray, current_dow: int) -> np.ndarray:
    """Assume `values` are consecutive daily points ending yesterday relative to current_dow."""
    n = values.size
    selected = []
    want_weekend = int(current_dow) >= 5
    for i, val in enumerate(values):
        days_ago = n - i
        hist_dow = (int(current_dow) - days_ago) % 7
        if want_weekend and hist_dow >= 5:
            selected.append(float(val))
        elif (not want_weekend) and hist_dow < 5:
            selected.append(float(val))
    return np.asarray(selected, dtype=float)


def _select_history(history: Iterable[float], context: dict[str, Any]) -> tuple[np.ndarray, str]:
    values = _as_float_array(history)
    same_segment = context.get("same_segment_history")
    if same_segment is not None:
        seg = _as_float_array(same_segment)
        if seg.size >= 3:
            return seg, "same_segment_history"

    current_dow = context.get("day_of_week")
    if current_dow is None or values.size < 6:
        return values, "raw"

    mean = float(np.mean(values))
    std = float(np.std(values))
    cv = (std / abs(mean)) if mean != 0 else 0.0
    # Homogeneous history is already segmented (e.g. caller pre-filtered Saturday).
    if cv < 0.18:
        return values, "raw_homogeneous"

    segmented = _extract_same_segment(values, int(current_dow))
    if segmented.size >= 3:
        return segmented, "same_weekday_or_weekend"
    return values, "raw"


def _relative_drop(current: float, baseline: np.ndarray, drop_ratio: float = 0.5) -> dict[str, Any]:
    if baseline.size == 0:
        return {"is_anomaly": False, "score": 0.0, "reason": "empty_baseline"}
    median = float(np.median(baseline))
    if median == 0:
        score = float("inf") if float(current) != 0 else 0.0
        return {"is_anomaly": bool(score > 0), "score": score, "reason": "zero_median"}
    ratio = float(current) / median
    # Flag large drops (volume) and large spikes.
    if ratio <= drop_ratio:
        score = (drop_ratio - ratio) / max(drop_ratio, 1e-9) * 4.0
        return {
            "is_anomaly": True,
            "score": float(max(score, 3.1)),
            "reason": f"relative_ratio={ratio:.3f} vs median={median:.3f} (drop)",
        }
    if ratio >= (1.0 / drop_ratio):
        score = (ratio - 1.0) * 2.0
        return {
            "is_anomaly": True,
            "score": float(max(score, 3.1)),
            "reason": f"relative_ratio={ratio:.3f} vs median={median:.3f} (spike)",
        }
    return {
        "is_anomaly": False,
        "score": float(abs(1.0 - ratio)),
        "reason": f"relative_ratio={ratio:.3f} vs median={median:.3f}",
    }


def auto_detector(
    current: float,
    history: Iterable[float],
    *,
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(context or {})
    raw = _as_float_array(history)
    baseline, source = _select_history(history, context)
    mad = mad_detector(current, baseline, threshold=max(threshold, 3.5))
    zscore = zscore_detector(current, baseline, threshold=threshold)
    drop = _relative_drop(current, baseline)
    raw_mad = mad_detector(current, raw, threshold=max(threshold, 3.5))
    raw_drop = _relative_drop(current, raw)

    votes = [mad, zscore, drop]
    segmented_hit = bool(
        mad["is_anomaly"] or drop["is_anomaly"] or (zscore["is_anomaly"] and mad["score"] >= 2.5)
    )
    raw_hit = bool(raw_mad["is_anomaly"] or raw_drop["is_anomaly"])
    is_anomaly = segmented_hit

    # A spike versus a seasonal bucket that still matches the overall baseline is
    # usually a calendar/segment mismatch (e.g. weekday-sized batch on Saturday),
    # not a true incident. Drops versus the seasonal bucket are kept.
    if is_anomaly and source != "raw" and source != "raw_homogeneous" and not raw_hit:
        seg_median = float(np.median(baseline)) if baseline.size else 0.0
        if float(current) >= seg_median:
            is_anomaly = False
            source = f"{source}+suppressed_segment_spike"

    score = float(max(mad["score"], zscore["score"], drop["score"]))

    reason = (
        f"history={source}; {mad['reason']}; {drop['reason']}"
    )
    if context.get("metric_name"):
        reason += f"; metric={context['metric_name']}"
    if context.get("day_of_week") is not None:
        reason += f"; day_of_week={context['day_of_week']}"
    if context.get("trend"):
        reason += f"; trend={context['trend']}"
    if context.get("known_event"):
        reason += f"; known_event={context['known_event']}"
        # Planned event: keep the score, but do not page a known pattern as an incident.
        is_anomaly = False
        reason += "; suppressed_known_event=true"

    return {
        "is_anomaly": bool(is_anomaly),
        "score": score,
        "method": f"auto:mad+weekday+relative({source})",
        "reason": reason,
        "components": {
            "mad": mad,
            "zscore": zscore,
            "relative": drop,
            "raw_mad": raw_mad,
            "history_source": source,
            "history_n": int(baseline.size),
        },
        "votes": votes,
    }


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if method == "mad":
        return mad_detector(current, history)
    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method == "ewma":
        return ewma_detector(current, history, threshold=threshold)
    if method == "auto":
        return auto_detector(current, history, threshold=threshold, context=context)
    raise ValueError(f"Unsupported method: {method}")
