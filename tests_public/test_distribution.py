from student_api import detect_distribution


def test_extreme_mean_shift_detected():
    baseline = [9, 10, 11, 10, 10]
    current = [190, 200, 210, 205]
    assert detect_distribution(current, baseline)["is_anomaly"] is True


def test_same_mean_shape_shift_detected():
    baseline = list(range(1, 21))
    current = [1] * 10 + [20] * 10
    result = detect_distribution(current, baseline)
    assert result["is_anomaly"] is True
    assert "ks" in result or "psi" in result["reason"]
