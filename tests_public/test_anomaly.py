from student_api import detect_metric


def test_large_volume_drop_is_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(300, history, method="zscore")
    assert result["is_anomaly"] is True


def test_stable_value_is_not_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(1002, history, method="zscore")
    assert result["is_anomaly"] is False


def test_mad_catches_drop_when_history_has_outlier():
    history = [1000, 1010, 995, 1008, 1004, 1012, 5000]
    mad = detect_metric(300, history, method="mad")
    zscore = detect_metric(300, history, method="zscore")
    assert mad["is_anomaly"] is True
    # Evidence: MAD still fires when a single outlier inflates z-score std.
    assert mad["score"] > zscore["score"]


def _daily_history_ending_friday() -> list[int]:
    # Mon..Sun repeated, then drop Sat/Sun so the series ends yesterday=Friday
    # when the current point is Saturday (day_of_week=5).
    week = [1000, 1010, 995, 1005, 998, 430, 440]
    return (week * 3)[:-2]


def test_auto_does_not_flag_normal_saturday():
    result = detect_metric(
        435,
        _daily_history_ending_friday(),
        method="auto",
        context={"metric_name": "row_count", "day_of_week": 5},
    )
    assert result["is_anomaly"] is False


def test_auto_flags_saturday_volume_collapse():
    result = detect_metric(
        80,
        _daily_history_ending_friday(),
        method="auto",
        context={"metric_name": "row_count", "day_of_week": 5},
    )
    assert result["is_anomaly"] is True


def test_auto_uses_same_segment_history():
    result = detect_metric(
        250,
        [1000, 1010, 995, 1008, 1004, 1012, 998],
        method="auto",
        context={"day_of_week": 5, "same_segment_history": [245, 255, 250, 248, 252, 260, 247]},
    )
    assert result["is_anomaly"] is False
