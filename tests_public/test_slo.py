import pytest
from student_api import multiwindow_burn, slo_status


def test_burn_rate_math():
    result = slo_status(0.995, bad_events=2, total_events=100)
    assert result["allowed_bad_rate"] == pytest.approx(0.005)
    assert result["actual_bad_rate"] == pytest.approx(0.02)
    assert result["burn_rate"] == pytest.approx(4.0)
    assert result["breached"] is True


def test_zero_events_is_safe():
    result = slo_status(0.99, bad_events=0, total_events=0)
    assert result["burn_rate"] == 0
    assert result["breached"] is False


def test_transient_spike_does_not_page():
    result = multiwindow_burn(20.0, 0.5)
    assert result["page"] is False
    assert result["reason"] == "transient_spike"


def test_sustained_fast_burn_pages():
    result = multiwindow_burn(14.4, 6.0)
    assert result["page"] is True
    assert result["severity"] == "critical"
    assert result["reason"] == "sustained_fast_burn"


def test_sustained_elevated_burn_pages():
    result = multiwindow_burn(6.0, 3.0)
    assert result["page"] is True
    assert result["reason"] == "sustained_elevated_burn"
