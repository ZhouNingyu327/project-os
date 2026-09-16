from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QualityReport:
    evaluator: str
    overall_score: float
    correctness: float
    usability: float
    visual: float
    performance: float
    evidence: list[dict[str, object]]
    evidence_score: float = 0.0
    originality: float = 0.0
    consistency: float = 0.0
    assessments: list[dict[str, object]] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "evaluator": self.evaluator,
            "overall_score": self.overall_score,
            "correctness": self.correctness,
            "usability": self.usability,
            "visual": self.visual,
            "performance": self.performance,
            "evidence": self.evidence,
            "evidence_score": self.evidence_score,
            "originality": self.originality,
            "consistency": self.consistency,
            "assessments": self.assessments or [],
        }


class MockWebsiteEvaluator:
    """Deterministic stand-in for independent browser/visual/content evaluators."""

    def evaluate(self, html: str) -> QualityReport:
        evidence: list[dict[str, object]] = []
        correctness = 10.0
        usability = 7.0
        visual = 6.0
        performance = 10.0
        if not re.search(r"<title>[^<]+</title>", html, re.I):
            correctness -= 3
            evidence.append({"issue": "missing_title", "dimension": "correctness", "severity": 3})
        if not re.search(r'<meta\s+name=["\']description["\']', html, re.I):
            correctness -= 2
            evidence.append({"issue": "missing_meta_description", "dimension": "correctness", "severity": 2})
        if not re.search(r"<h1[\s>]", html, re.I):
            usability -= 2
            evidence.append({"issue": "missing_h1", "dimension": "usability", "severity": 2})
        if not re.search(r"<nav\b[^>]*\baria-label=", html, re.I):
            usability -= 2
            evidence.append({"issue": "unlabeled_navigation", "dimension": "usability", "severity": 2})
        if not re.search(r"<main\b", html, re.I):
            usability -= 1
            evidence.append({"issue": "missing_main_landmark", "dimension": "usability", "severity": 1})
        if "<style" not in html.lower() and 'rel="stylesheet"' not in html.lower():
            visual -= 2
            evidence.append({"issue": "no_declared_style", "dimension": "visual", "severity": 2})
        if len(html) > 300_000:
            performance -= 3
            evidence.append({"issue": "large_document", "dimension": "performance", "severity": 3})
        score = round(correctness * 0.35 + usability * 0.30 + visual * 0.20 + performance * 0.15, 2)
        return QualityReport("mock-html-v1", score, correctness, usability, visual, performance, evidence)
