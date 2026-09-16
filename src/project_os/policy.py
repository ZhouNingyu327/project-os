"""Policy-controlled approval for candidate website changes.

The policy is intentionally deterministic and independent of a generator.  A
future coding model can propose patches, but it must not relax the gate that
decides whether they may touch a workspace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


QUALITY_DIMENSIONS = (
    "correctness",
    "evidence_score",
    "usability",
    "visual",
    "originality",
    "performance",
    "consistency",
)


@dataclass(frozen=True)
class PolicyDecision:
    accepted: bool
    reasons: tuple[str, ...]

    @property
    def reason(self) -> str:
        return " ".join(self.reasons)


@dataclass(frozen=True)
class DecisionPolicy:
    """Minimum safety policy for any automatically applied candidate.

    ``protected_dimensions`` may never regress. Other dimensions are permitted
    a small, explicit tolerance because some measurements are noisy. A caller
    can make the policy stricter by setting the tolerance to zero.
    """

    minimum_overall_gain: float = 0.01
    protected_dimensions: tuple[str, ...] = ("correctness", "evidence_score", "usability")
    maximum_nonprotected_drop: float = 0.25
    require_validation: bool = True

    def evaluate(
        self,
        baseline: Mapping[str, object],
        candidate: Mapping[str, object],
        *,
        validation_passed: bool | None,
    ) -> PolicyDecision:
        reasons: list[str] = []
        baseline_score = float(baseline["overall_score"])
        candidate_score = float(candidate["overall_score"])
        if candidate_score < baseline_score + self.minimum_overall_gain:
            reasons.append(
                f"overall score must increase by at least {self.minimum_overall_gain:.2f} "
                f"({baseline_score:.2f} → {candidate_score:.2f})"
            )

        for dimension in QUALITY_DIMENSIONS:
            if dimension not in baseline or dimension not in candidate:
                continue
            before, after = float(baseline[dimension]), float(candidate[dimension])
            if dimension in self.protected_dimensions and after < before:
                reasons.append(f"protected {dimension} regressed ({before:.2f} → {after:.2f})")
            elif dimension not in self.protected_dimensions and after < before - self.maximum_nonprotected_drop:
                reasons.append(f"{dimension} regressed beyond tolerance ({before:.2f} → {after:.2f})")

        if self.require_validation and validation_passed is not True:
            reasons.append("candidate did not pass required validation")

        if reasons:
            return PolicyDecision(False, tuple(reasons))
        return PolicyDecision(True, ("candidate satisfied the configured approval policy",))
