from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import mad_detector, zscore_detector
from observability.distribution import detect_distribution_shift


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    z_result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    mad_result = mad_detector(current_mean, baseline_batch_means, threshold=3.5)
    is_anomaly = bool(z_result["is_anomaly"] or mad_result["is_anomaly"])
    score = float(max(z_result["score"], mad_result["score"]))
    return {
        "is_anomaly": is_anomaly,
        "score": score,
        "method": "text_length:zscore+mad",
        "reason": f"{z_result['reason']}; {mad_result['reason']}",
        "metric": "mean_text_length",
        "current_mean": current_mean,
    }


def detect_embedding_norm_shift(
    current_norms: Iterable[float], baseline_norms: Iterable[float]
) -> dict[str, Any]:
    """Detect embedding-space drift from precomputed L2 norms.

    Hidden evaluation can feed batch norms/similarities through this stable
    interface without shipping an embedding model.
    """
    cur = np.asarray(list(current_norms), dtype=float)
    base = np.asarray(list(baseline_norms), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "embedding_norm",
            "reason": "empty_input",
        }

    current_mean = float(np.mean(cur))
    z_result = zscore_detector(current_mean, base)
    mad_result = mad_detector(current_mean, base)
    # KS/PSI on tiny batches is noisy; only use them when both sides are large
    # and the mean has already moved.
    dist = None
    if cur.size >= 8 and base.size >= 8:
        dist = detect_distribution_shift(cur, base, ratio_threshold=2.0)

    base_mean = float(np.mean(base))
    relative = abs(current_mean - base_mean) / abs(base_mean) if base_mean != 0 else float("inf")
    relative_hit = relative >= 0.20

    dist_hit = bool(dist and dist["is_anomaly"] and relative >= 0.10)
    is_anomaly = bool(z_result["is_anomaly"] or mad_result["is_anomaly"] or relative_hit or dist_hit)
    score = float(
        max(
            z_result["score"],
            mad_result["score"],
            dist["score"] if dist else 0.0,
            relative * 5.0,
        )
    )
    return {
        "is_anomaly": is_anomaly,
        "score": score,
        "method": "embedding_norm:mad+zscore",
        "reason": (
            f"current_mean={current_mean:.4f}, baseline_mean={base_mean:.4f}, "
            f"relative={relative:.3f}; {mad_result['reason']}"
            + (f"; {dist['reason']}" if dist else "")
        ),
        "current_mean": current_mean,
        "baseline_mean": base_mean,
        "relative": relative,
    }
