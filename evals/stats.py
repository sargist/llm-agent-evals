"""Small, dependency-free statistics used by the reports."""

from __future__ import annotations

import math
from collections.abc import Sequence


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's kappa for two raters over the same items. None when undefined (one label only)."""
    if len(a) != len(b) or not a:
        raise ValueError("need two label sequences of the same, non-zero length")
    n = len(a)
    labels = sorted(set(a) | set(b))
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    expected = sum((list(a).count(lbl) / n) * (list(b).count(lbl) / n) for lbl in labels)
    if math.isclose(expected, 1.0):
        return None
    return round((observed - expected) / (1 - expected), 3)


def binary_rates(
    truth: Sequence[str], predicted: Sequence[str], positive: str = "fail"
) -> dict[str, float | None]:
    """Accuracy, TPR (sensitivity) and TNR (specificity), with `positive` as the positive class."""
    pairs = list(zip(truth, predicted, strict=True))
    tp = sum(t == positive and p == positive for t, p in pairs)
    tn = sum(t != positive and p != positive for t, p in pairs)
    pos = sum(t == positive for t, _ in pairs)
    neg = len(pairs) - pos
    return {
        "n": len(pairs),
        "positives": pos,
        "accuracy": round((tp + tn) / len(pairs), 3) if pairs else None,
        "tpr": round(tp / pos, 3) if pos else None,
        "tnr": round(tn / neg, 3) if neg else None,
    }


def pass_at_1(runs: dict[str, list[bool]]) -> float:
    """Mean pass rate over all runs of all cases."""
    flat = [r for results in runs.values() for r in results]
    return round(sum(flat) / len(flat), 3) if flat else 0.0


def pass_hat_k(runs: dict[str, list[bool]]) -> float:
    """pass^k: share of cases that pass in every one of their k runs."""
    return round(sum(all(r) for r in runs.values()) / len(runs), 3) if runs else 0.0


def consistency(runs: dict[str, list[bool]]) -> float:
    """Share of cases whose k runs all have the same outcome (all pass or all fail)."""
    return round(sum(len(set(r)) == 1 for r in runs.values()) / len(runs), 3) if runs else 0.0


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return round(ordered[rank - 1], 3)
