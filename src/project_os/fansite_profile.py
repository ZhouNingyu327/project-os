from __future__ import annotations

from pathlib import Path

from .evaluators import QualityReport
from .improver import Improvement
from .quality_agents import EvaluationContext, MetaEvaluator, default_fansite_agents
from .site_tools import WebsiteToolchain


class FansiteEvaluator:
    """A seven-agent, source-level evaluator for the Astro fansite profile.

    The visual and originality agents declare lower confidence because they are
    source fallbacks. A screenshot-based agent may replace either one without
    changing the aggregate or approval-policy interfaces.
    """

    def __init__(self, project_root: Path, *, target_url: str | None = None, enable_runtime_tools: bool = False) -> None:
        self.project_root = project_root
        self.meta_evaluator = MetaEvaluator(default_fansite_agents())
        self.toolchain = WebsiteToolchain(target_url=target_url, enable_runtime=enable_runtime_tools)

    def evaluate(self, layout: str) -> QualityReport:
        context = EvaluationContext(
            source=layout,
            project_root=self.project_root,
            brand_marker="只有音乐和真心最重要",
            tool_reports=self.toolchain.collect(self.project_root, layout),
        )
        return self.meta_evaluator.evaluate(context)


class FansiteImprover:
    """Produces one narrowly scoped, reversible repair per iteration."""

    def improve(self, source: str, issues: list[dict[str, object]]) -> Improvement | None:
        import re

        names = {str(issue["issue"]) for issue in issues}
        if "duplicate_splash_assets" in names:
            removed = 0

            def remove_duplicate(match: re.Match[str]) -> str:
                nonlocal removed
                tag = match.group(0)
                if "splash-bg" in tag and "object-cover" in tag and re.search(r'src="/images/封面图[23]\.jpg"', tag):
                    removed += 1
                    return ""
                return tag

            candidate = re.sub(r"<img\b[^>]*>", remove_duplicate, source)
            if removed == 2:
                return Improvement("Remove duplicate cropped splash images", "Eliminates duplicate assets that reintroduce cropped artwork.", candidate)
        if "decorative_splash_images_need_empty_alt" in names:
            def add_empty_alt(match: re.Match[str]) -> str:
                tag = match.group(0)
                return tag if "splash-bg" not in tag or " alt=" in tag else tag[:-1] + ' alt="" />'

            candidate = re.sub(r"<img\b[^>]*>", add_empty_alt, source)
            if candidate != source:
                return Improvement("Mark splash artwork decorative", "Adds empty alt text so screen readers skip decorative background images.", candidate)
        return None
