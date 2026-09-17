"""Bounded worker agents used by the LangGraph supervisor.

The quality specialists remain independent inside ``FansiteEvaluator``.  This
module gives them one explicit boundary (``QualityAssessmentAgent``), and
gives research and safe repair planning a separate boundary
(``TargetedImprovementAgent``).  Neither worker can commit a website change.
Only the supervisor's policy-gated decide node can do that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .evaluators import QualityReport
from .improver import Improvement


class WebsiteEvaluator(Protocol):
    def evaluate(self, source: str) -> QualityReport: ...


class WebsiteImprover(Protocol):
    def improve(self, source: str, issues: list[dict[str, object]]) -> Improvement | None: ...


class VerifiedResearcher(Protocol):
    def research(self, project_id: int, proposal: Any) -> Any: ...


@dataclass(frozen=True)
class Assessment:
    report: QualityReport
    findings: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class ImprovementBrief:
    """A durable, non-executing research or repair instruction."""

    issue: str
    dimension: str
    severity: int
    objective: str
    suggested_tools: tuple[str, ...]
    requires_external_evidence: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "issue": self.issue,
            "dimension": self.dimension,
            "severity": self.severity,
            "objective": self.objective,
            "suggested_tools": list(self.suggested_tools),
            "requires_external_evidence": self.requires_external_evidence,
        }


class QualityAssessmentAgent:
    """One worker boundary around the seven read-only quality specialists."""

    name = "quality-assessment-agent"

    def __init__(self, evaluator: WebsiteEvaluator) -> None:
        self.evaluator = evaluator

    def assess(self, source: str) -> Assessment:
        report = self.evaluator.evaluate(source)
        findings = tuple(sorted(report.evidence, key=lambda item: int(item.get("severity", 0)), reverse=True))
        return Assessment(report, findings)


class TargetedImprovementAgent:
    """Translates an assessment into a constrained research/repair brief.

    It may propose a deterministic local repair, but it never invents factual
    content or publishes web research. Evidence-related plans must go through
    ``ResearchPipeline`` and its two-source gate before a content writer is
    allowed to stage a draft.
    """

    name = "targeted-improvement-agent"

    _TOOL_HINTS = {
        "published_news_without_sources": ("web-search", "robots-aware-fetch", "evidence-verifier"),
        "published_news_needing_cross_check": ("web-search", "robots-aware-fetch", "evidence-verifier"),
        "stage_manifest_not_configured": ("programme-inventory", "discovery-ocr", "web-search", "evidence-verifier"),
        "stage_manifest_coverage_incomplete": ("programme-inventory", "discovery-ocr", "web-search", "evidence-verifier"),
        "expected_stage_missing_from_archive": ("programme-inventory", "discovery-ocr", "web-search", "evidence-verifier"),
        "expected_stage_not_verified": ("web-search", "robots-aware-fetch", "evidence-verifier"),
        "factual_collections_missing_provenance": ("content-inventory", "web-search", "robots-aware-fetch", "evidence-verifier"),
        "large_public_asset_budget": ("lighthouse", "asset-inventory", "image-optimizer"),
        "duplicate_splash_assets": ("playwright-screenshot", "asset-inventory", "source-editor"),
        "decorative_splash_images_need_empty_alt": ("accessibility-source", "source-editor"),
        "missing_title_element": ("markup-correctness", "source-editor"),
        "missing_description_metadata": ("markup-correctness", "source-editor"),
        "missing_project_brand_voice": ("screenshot-review", "content-inventory", "human-review"),
    }

    def __init__(self, improver: WebsiteImprover, researcher: VerifiedResearcher | None = None) -> None:
        self.improver = improver
        self.researcher = researcher

    def brief_for(self, finding: dict[str, object]) -> ImprovementBrief:
        issue = str(finding["issue"])
        dimension = str(finding.get("dimension", "quality"))
        external = issue.startswith("published_news_") or issue.startswith("stage_manifest_") or issue.startswith("expected_stage_") or issue == "factual_collections_missing_provenance"
        tools = self._TOOL_HINTS.get(issue, ("repository-inventory", "source-editor"))
        if issue.startswith(("stage_manifest_", "expected_stage_")):
            objective = "Build or complete the programme inventory from discovery leads, then independently verify each unresolved stage identity before any archive draft is eligible for review."
        elif external:
            objective = "Collect two independent high-trust sources and verify each affected published claim before drafting a correction."
        else:
            objective = f"Resolve {issue} without reducing protected quality dimensions."
        return ImprovementBrief(issue, dimension, int(finding.get("severity", 1)), objective, tools, external)

    def propose_local_repair(self, source: str, findings: tuple[dict[str, object], ...]) -> Improvement | None:
        # Facts and externally researched text are intentionally excluded here.
        # The existing research pipeline handles them as verified drafts.
        safe_findings = [item for item in findings if not (str(item["issue"]).startswith("published_news_") or str(item["issue"]).startswith("stage_manifest_") or str(item["issue"]).startswith("expected_stage_"))]
        return self.improver.improve(source, safe_findings)

    def collect_verified_evidence(self, project_id: int, proposal: Any) -> Any:
        """Run the injected research pipeline; its verifier controls publication.

        ``proposal`` is deliberately supplied by a content-specific planner or
        human review step. A generic quality finding is not enough evidence to
        fabricate a news title, date, or claim.
        """
        if self.researcher is None:
            raise RuntimeError("No verified research pipeline is configured for this improvement agent.")
        return self.researcher.research(project_id, proposal)
