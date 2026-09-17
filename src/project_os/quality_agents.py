"""Independent, read-only quality agents and their deterministic Meta Evaluator."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .evaluators import QualityReport
from .site_tools import ToolReport


@dataclass(frozen=True)
class EvaluationContext:
    source: str
    project_root: Path
    brand_marker: str = ""
    tool_reports: dict[str, ToolReport] | None = None

    def tool(self, name: str) -> ToolReport | None:
        return (self.tool_reports or {}).get(name)


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
        markup = context.tool("markup-correctness")
        if not markup or markup.status != "available":
            return AgentAssessment(self.name, self.dimension, 10.0, 0.35, ())
        missing = len(markup.findings)
        findings = tuple({**finding, "dimension": "correctness"} for finding in markup.findings)
        return AgentAssessment(self.name, self.dimension, 10.0 - missing * 1.0, 0.65, findings)


class EvidenceAgent:
    name, dimension = "evidence-agent", "evidence_score"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        report = context.tool("content-evidence")
        metrics = report.metrics if report and report.status == "available" else {}
        public_news = int(metrics.get("public_news", 0))
        verified = int(metrics.get("verified_news", 0))
        pending = int(metrics.get("pending_news", 0))
        unsourced = int(metrics.get("unsourced_news", 0))
        points = float(metrics.get("evidence_points", 0))
        stages = int(metrics.get("stages", 0))
        verified_stages = int(metrics.get("verified_stages", 0))
        pending_stages = int(metrics.get("pending_stages", 0))
        legacy_stages = int(metrics.get("legacy_stages", 0))
        findings: list[dict[str, object]] = []
        if unsourced:
            findings.append({"issue": "published_news_without_sources", "dimension": "evidence", "severity": 3, "covered": public_news - unsourced, "total": public_news})
        if pending:
            findings.append({"issue": "published_news_needing_cross_check", "dimension": "evidence", "severity": 2, "pending": pending, "verified": verified, "total": public_news})
        if not public_news:
            findings.append({"issue": "no_news_evidence_to_assess", "dimension": "evidence", "severity": 3})
        if legacy_stages:
            findings.append({"issue": "stage_archive_missing_sources", "dimension": "evidence", "severity": 2, "covered": verified_stages + pending_stages, "total": stages})
        if pending_stages:
            findings.append({"issue": "stage_archive_needing_cross_check", "dimension": "evidence", "severity": 2, "pending": pending_stages, "total": stages})
        covered_units = public_news + stages
        covered_points = points + verified_stages + pending_stages * 0.45
        score = round(10 * covered_points / covered_units, 2) if covered_units else 0.0
        return AgentAssessment(self.name, self.dimension, score, 0.8 if report else 0.2, tuple(findings))


class UsabilityAgent:
    name, dimension = "usability-agent", "usability"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", context.source)
        missing_alt = sum(" alt=" not in tag for tag in tags)
        if not missing_alt:
            return AgentAssessment(self.name, self.dimension, 9.5, 0.75 if context.tool("accessibility-source") else 0.35, ())
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
            screenshot = context.tool("playwright-screenshot")
            confidence = 0.65 if screenshot and screenshot.status == "available" else 0.45
            return AgentAssessment(self.name, self.dimension, 8.5, confidence, ())
        finding = {"issue": "duplicate_splash_assets", "dimension": "visual", "severity": 3, "duplicates": duplicates}
        return AgentAssessment(self.name, self.dimension, 6.0, 0.45, (finding,))


class OriginalityAgent:
    name, dimension = "originality-agent", "originality"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        if not context.brand_marker or context.brand_marker in context.source:
            design = context.tool("design-system-inventory")
            confidence = 0.5 if design and int(design.metrics.get("design_tokens", 0)) else 0.35
            return AgentAssessment(self.name, self.dimension, 8.0, confidence, ())
        finding = {"issue": "missing_project_brand_voice", "dimension": "originality", "severity": 2}
        return AgentAssessment(self.name, self.dimension, 6.5, 0.35, (finding,))


class PerformanceAgent:
    name, dimension = "performance-agent", "performance"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", context.source)
        sources = [match.group(1) for tag in tags if (match := re.search(r'src="([^"]+)"', tag))]
        lighthouse = context.tool("lighthouse")
        asset = context.tool("asset-performance")
        score = float(lighthouse.metrics["performance"]) if lighthouse and lighthouse.status == "available" and "performance" in lighthouse.metrics else (8.5 if len(sources) != len(set(sources)) else 9.0)
        findings_list: list[dict[str, object]] = []
        if asset and int(asset.metrics.get("public_bytes", 0)) > 5 * 1024 * 1024 and not (lighthouse and lighthouse.status == "available"):
            score -= 0.5
            findings_list.append({"issue": "large_public_asset_budget", "dimension": "performance", "severity": 2, "bytes": asset.metrics["public_bytes"]})
        if "sessionStorage.getItem('splashShown')" not in context.source:
            score -= 1.0
            findings_list.append({"issue": "splash_repeats_each_navigation", "dimension": "performance", "severity": 2})
        # A first-visit splash may be intentional, but a long forced wait delays the
        # page's actual content even after its assets have loaded.  Keep this source
        # check separate from Lighthouse so an offline evaluation can still flag it.
        splash_assignments = re.findall(r"var\s+minShow\s*=([^;]+);", context.source)
        splash_waits = [int(value) for assignment in splash_assignments for value in re.findall(r"\d+", assignment)]
        if splash_waits and max(splash_waits) > 2500:
            wait_ms = max(splash_waits)
            score -= 1.0
            findings_list.append({"issue": "long_first_visit_splash", "dimension": "performance", "severity": 2, "milliseconds": wait_ms})
        confidence = 0.9 if lighthouse and lighthouse.status == "available" else 0.55
        return AgentAssessment(self.name, self.dimension, max(0.0, score), confidence, tuple(findings_list))


class ConsistencyAgent:
    name, dimension = "consistency-agent", "consistency"
    def assess(self, context: EvaluationContext) -> AgentAssessment:
        inventory = context.tool("repository-inventory")
        confidence = 0.6 if inventory and inventory.status == "available" else 0.4
        return AgentAssessment(self.name, self.dimension, 8.5, confidence, ())


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
