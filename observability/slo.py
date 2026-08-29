"""SLO / error-budget math and Google SRE multi-window burn-rate policy."""
from __future__ import annotations

from typing import Any


# Google SRE Workbook starting point (30-day SLO):
# page if 14.4x over 1h AND 6x over 6h, or 6x over 6h AND 3x over 3d.
FAST_PAGE_SHORT = 14.4
FAST_PAGE_LONG = 6.0
ELEVATED_PAGE_SHORT = 6.0
ELEVATED_PAGE_LONG = 3.0
TICKET_SHORT = 3.0
TICKET_LONG = 1.0


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "sre",
) -> dict[str, Any]:
    """Page only when BOTH windows confirm a sustained burn.

    A short-window spike with a calm long window is treated as transient and
    does not page. This is the SRE workbook multi-window rule, compressed to
    the two burn-rate numbers exposed by the stable student API.
    """
    short = float(short_window_burn)
    long = float(long_window_burn)
    payload = {
        "short_window_burn": short,
        "long_window_burn": long,
        "policy": policy or "sre",
        "fast_page_rule": f"short>={FAST_PAGE_SHORT} and long>={FAST_PAGE_LONG}",
        "elevated_page_rule": f"short>={ELEVATED_PAGE_SHORT} and long>={ELEVATED_PAGE_LONG}",
    }

    if short >= FAST_PAGE_SHORT and long >= FAST_PAGE_LONG:
        payload.update(
            {
                "page": True,
                "severity": "critical",
                "reason": "sustained_fast_burn",
            }
        )
        return payload
    if short >= ELEVATED_PAGE_SHORT and long >= ELEVATED_PAGE_LONG:
        payload.update(
            {
                "page": True,
                "severity": "warning",
                "reason": "sustained_elevated_burn",
            }
        )
        return payload
    if short >= FAST_PAGE_SHORT and long < FAST_PAGE_LONG:
        payload.update(
            {
                "page": False,
                "severity": "info",
                "reason": "transient_spike",
            }
        )
        return payload
    if short >= TICKET_SHORT and long >= TICKET_LONG:
        payload.update(
            {
                "page": False,
                "severity": "warning",
                "reason": "slow_burn_ticket",
            }
        )
        return payload
    payload.update(
        {
            "page": False,
            "severity": "info",
            "reason": "within_error_budget",
        }
    )
    return payload
