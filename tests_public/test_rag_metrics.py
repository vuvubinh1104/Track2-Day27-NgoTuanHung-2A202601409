from student_api import rag_embedding_shift, rag_length_shift


def test_rag_length_collapse_is_detected():
    baseline_batch_means = [40, 42, 39, 41, 43, 40, 42]
    current_texts = ["x y", "a b c", "one two"]
    assert rag_length_shift(current_texts, baseline_batch_means)["is_anomaly"] is True


def test_rag_embedding_norm_collapse_is_detected():
    baseline = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01]
    current = [0.12, 0.10, 0.11, 0.09]
    result = rag_embedding_shift(current, baseline)
    assert result["is_anomaly"] is True
    assert result["score"] > 0


def test_stable_embedding_norms_are_not_anomalous():
    baseline = [1.00, 1.01, 0.99, 1.02, 0.98, 1.00, 1.01]
    current = [0.99, 1.00, 1.01]
    assert rag_embedding_shift(current, baseline)["is_anomaly"] is False
