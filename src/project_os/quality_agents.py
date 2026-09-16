"""Independent, read-only quality agents and their deterministic Meta Evaluator."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .evaluators import QualityReport


@dataclass(frozen=True)
class EvaluationContext:
    source: str
    project_root: Path
    brand_marker: str = ""


@dataclass(frozen=True)
class AgentAssessment:
    agent: str
    dimension: str
    score: float
    confidence: float
    findings: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {"agent": self.agent, "dimension": self.dimension, "score": self.score, "confidence": self.confidence, "findings": list(self.findings)}


class QualityAgent(Protocol):
    name: str
    dimension: str
    def assess(self, context: EvaluationContext) -> AgentAssessment: ...


class CorrectnessAgent:
    name, dimension = "correctness-agent", "correctness"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        return AgentAssessment(self.name, self.dimension, 10.0, 0.55, ())


class EvidenceAgent:
    name, dimension = "evidence-agent", "evidence_score"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        directory = context.project_root / "src" / "content" / "news"
        all_news = list(directory.glob("*.mdx")) if directory.exists() else []
        public_news = [path for path in all_news if "publicationStatus: 'review'" not in path.read_text(encoding="utf-8")]
        verified = pending = unsourced = 0
        points = 0.0
        for path in public_news:
            text = path.read_text(encoding="utf-8")
            sources = len(re.findall(r"^\s*(?:-\s*)?(?:sourceUrl|url):\s*['\"]?https?://", text, flags=re.MULTILINE))
            if "verificationStatus: 'verified'" in text and sources >= 2:
                verified += 1; points += 1.0
            elif sources:
                pending += 1; points += 0.45
            else:
                unsourced += 1
        findings: list[dict[str, object]] = []
        if unsourced:
            findings.append({"issue": "published_news_without_sources", "dimension": "evidence", "severity": 3, "covered": len(public_news) - unsourced, "total": len(public_news)})
        if pending:
            findings.append({"issue": "published_news_needing_cross_check", "dimension": "evidence", "severity": 2, "pending": pending, "verified": verified, "total": len(public_news)})
        if not public_news:
            findings.append({"issue": "no_news_evidence_to_assess", "dimension": "evidence", "severity": 3})
        score = round(10 * points / len(public_news), 2) if public_news else 0.0
        return AgentAssessment(self.name, self.dimension, score, 0.8, tuple(findings))


class UsabilityAgent:
    name, dimension = "usability-agent", "usability"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", context.source)
        missing_alt = sum(" alt=" not in tag for tag in tags)
        if not missing_alt:
            return AgentAssessment(self.name, self.dimension, 9.5, 0.75, ())
        finding = {"issue": "decorative_splash_images_need_empty_alt", "dimension": "usability", "severity": 2, "count": missing_alt}
        return AgentAssessment(self.name, self.dimension, 8.0, 0.75, (finding,))


class VisualStructureAgent:
    """A source fallback, explicitly lower confidence than screenshot evaluation."""
    name, dimension = "visual-structure-agent", "visual"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", context.source)
        sources = [match.group(1) for tag in tags if (match := re.search(r'src="([^"]+)"', tag))]
        duplicates = sorted({source for source in sources if sources.count(source) > 1})
        if not duplicates:
            return AgentAssessment(self.name, self.dimension, 8.5, 0.45, ())
        finding = {"issue": "duplicate_splash_assets", "dimension": "visual", "severity": 3, "duplicates": duplicates}
        return AgentAssessment(self.name, self.dimension, 6.0, 0.45, (finding,))


class OriginalityAgent:
    name, dimension = "originality-agent", "originality"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        if not context.brand_marker or context.brand_marker in context.source:
            return AgentAssessment(self.name, self.dimension, 8.0, 0.35, ())
        finding = {"issue": "missing_project_brand_voice", "dimension": "originality", "severity": 2}
        return AgentAssessment(self.name, self.dimension, 6.5, 0.35, (finding,))


class PerformanceAgent:
    name, dimension = "performance-agent", "performance"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", context.source)
        sources = [match.group(1) for tag in tags if (match := re.search(r'src="([^"]+)"', tag))]
        score = 8.5 if len(sources) != len(set(sources)) else 9.0
        findings: tuple[dict[str, object], ...] = ()
        if "sessionStorage.getItem('splashShown')" not in context.source:
            score -= 1.0
            findings = ({"issue": "splash_repeats_each_navigation", "dimension": "performance", "severity": 2},)
        return AgentAssessment(self.name, self.dimension, score, 0.55, findings)


class ConsistencyAgent:
    name, dimension = "consistency-agent", "consistency"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        return AgentAssessment(self.name, self.dimension, 8.5, 0.4, ())


class MetaEvaluator:
    """Runs the seven specialist agents concurrently; it never creates patches."""
    weights = {"correctness": 0.20, "evidence_score": 0.18, "usability": 0.16, "visual": 0.18, "originality": 0.10, "performance": 0.09, "consistency": 0.09}

    def __init__(self, agents: tuple[QualityAgent, ...]) -> None:
        dimensions = [agent.dimension for agent in agents]
        if len(dimensions) != len(set(dimensions)) or set(dimensions) != set(self.weights):
            raise ValueError("Meta Evaluator requires exactly one agent for every configured quality dimension.")
        self.agents = agents

    def evaluate(self, context: EvaluationContext) -> QualityReport:
        with ThreadPoolExecutor(max_workers=len(self.agents), thread_name_prefix="quality-agent") as pool:
            futures = {agent.name: pool.submit(agent.assess, context) for agent in self.agents}
            assessments = [futures[agent.name].result() for agent in self.agents]
        scores = {assessment.dimension: assessment.score for assessment in assessments}
        findings = [{**finding, "agent": assessment.agent} for assessment in assessments for finding in assessment.findings]
        overall = round(sum(scores[dimension] * weight for dimension, weight in self.weights.items()), 2)
        return QualityReport("fansite-multi-agent-v1", overall, scores["correctness"], scores["usability"], scores["visual"], scores["performance"], findings, evidence_score=scores["evidence_score"], originality=scores["originality"], consistency=scores["consistency"], assessments=[assessment.as_dict() for assessment in assessments])


def default_fansite_agents() -> tuple[QualityAgent, ...]:
    return (CorrectnessAgent(), EvidenceAgent(), UsabilityAgent(), VisualStructureAgent(), OriginalityAgent(), PerformanceAgent(), ConsistencyAgent())
