from evals.calibrate import SPLITS, agreement, load_items, to_judge_item, verdict_of
from evals.judge import CRITERIA


def test_calibration_set_is_valid_and_balanced():
    items = load_items()
    assert len(items) >= 15
    for split in SPLITS:
        verdicts = [verdict_of(i["human"]) for i in items if i["split"] == split]
        assert "pass" in verdicts and "fail" in verdicts, split
    for criterion in CRITERIA:
        assert sum(i["human"][criterion] == "fail" for i in items) >= 2, criterion
    for item in items:
        assert item["rationale"] and item["answer"], item["id"]
        to_judge_item(item)  # every item renders as judge input


def row(human_fails, judge_fails, verdict=None):
    human = {c: "fail" if c in human_fails else "pass" for c in CRITERIA}
    judge = {c: "fail" if c in judge_fails else "pass" for c in CRITERIA}
    return {
        "human": human,
        "judge": {"verdict": verdict or ("fail" if judge_fails else "pass"), "criteria": judge},
    }


def test_agreement_reports_per_criterion_tpr_and_tnr():
    rows = [
        row({"language"}, {"language"}),  # caught
        row({"injection"}, set()),  # missed defect
        row(set(), {"policy"}),  # false alarm
        row(set(), set()),
    ]
    stats = agreement(rows)
    assert stats["n"] == 4
    assert stats["overall"]["tpr"] == 0.5 and stats["overall"]["tnr"] == 0.5
    assert stats["criteria"]["language"]["tpr"] == 1.0
    assert stats["criteria"]["injection"]["tpr"] == 0.0
    assert stats["criteria"]["policy"]["tnr"] == 0.75 and stats["criteria"]["policy"]["tpr"] is None


def test_judge_errors_are_excluded_and_counted():
    rows = [
        row({"language"}, {"language"}),
        {"human": row(set(), set())["human"], "judge": {"verdict": "error"}},
    ]
    stats = agreement(rows)
    assert stats["n"] == 1 and stats["judge_errors"] == 1
