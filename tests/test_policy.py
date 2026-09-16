import unittest

from project_os.policy import DecisionPolicy


def report(**overrides: float) -> dict[str, float]:
    baseline = {
        "overall_score": 8.0, "correctness": 9.0, "evidence_score": 7.0,
        "usability": 8.0, "visual": 8.0, "originality": 8.0,
        "performance": 8.0, "consistency": 8.0,
    }
    baseline.update(overrides)
    return baseline


class DecisionPolicyTest(unittest.TestCase):
    def test_accepts_validated_improvement(self) -> None:
        decision = DecisionPolicy(require_validation=True).evaluate(
            report(), report(overall_score=8.2, visual=8.2), validation_passed=True
        )
        self.assertTrue(decision.accepted)

    def test_rejects_protected_regression_and_missing_validation(self) -> None:
        decision = DecisionPolicy(require_validation=True).evaluate(
            report(), report(overall_score=8.2, correctness=8.9), validation_passed=False
        )
        self.assertFalse(decision.accepted)
        self.assertIn("protected correctness regressed", decision.reason)
        self.assertIn("did not pass required validation", decision.reason)
