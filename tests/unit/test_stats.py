import pytest

from evals.stats import binary_rates, cohen_kappa, consistency, pass_at_1, pass_hat_k, percentile


def test_cohen_kappa_textbook_example():
    # 50 items: both yes 20, A yes/B no 5, A no/B yes 10, both no 15 -> p_o 0.7, p_e 0.5, kappa 0.4
    a = ["yes"] * 25 + ["no"] * 25
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4)


def test_cohen_kappa_bounds_and_undefined_case():
    assert cohen_kappa(["pass", "fail"], ["pass", "fail"]) == 1.0
    assert cohen_kappa(["pass", "fail"], ["fail", "pass"]) == -1.0
    assert cohen_kappa(["pass", "pass"], ["pass", "pass"]) is None
    with pytest.raises(ValueError):
        cohen_kappa(["pass"], [])


def test_binary_rates_use_fail_as_the_positive_class():
    human = ["fail", "fail", "pass", "pass", "pass"]
    judge = ["fail", "pass", "pass", "pass", "fail"]
    rates = binary_rates(human, judge)
    assert rates == {"n": 5, "positives": 2, "accuracy": 0.6, "tpr": 0.5, "tnr": pytest.approx(0.667)}
    assert binary_rates(["pass"], ["pass"])["tpr"] is None


def test_repeat_metrics():
    runs = {"a": [True, True, True], "b": [True, False, True], "c": [False, False, False]}
    assert pass_at_1(runs) == pytest.approx(5 / 9, abs=1e-3)
    assert pass_hat_k(runs) == pytest.approx(1 / 3, abs=1e-3)
    assert consistency(runs) == pytest.approx(2 / 3, abs=1e-3)


def test_percentile_nearest_rank():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert percentile(values, 50) == 5
    assert percentile(values, 95) == 10
    assert percentile([], 50) is None
