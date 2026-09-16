from __future__ import annotations

import re

from .evaluators import QualityReport
from .improver import Improvement


class AstroSplashEvaluator:
    """A real project-specific evaluator; browser rendering is the next adapter."""

    def evaluate(self, source: str) -> QualityReport:
        image_sources = re.findall(r'<img\s+[^>]*class="[^"]*splash-bg[^"]*"[^>]*src="([^"]+)"[^>]*>', source)
        # Astro files may put src before class, so collect either attribute order.
        image_sources = re.findall(r'<img\b(?=[^>]*\bsplash-bg\b)(?=[^>]*\bsrc="([^"]+)")[^>]*>', source)
        duplicates = sorted({src for src in image_sources if image_sources.count(src) > 1})
        evidence: list[dict[str, object]] = []
        visual = 8.0
        performance = 9.0
        if duplicates:
            visual -= 2.5
            performance -= 0.5
            evidence.append({
                "issue": "duplicate_splash_assets",
                "dimension": "visual",
                "severity": 3,
                "duplicates": duplicates,
            })
        score = round(10 * 0.25 + 9 * 0.25 + visual * 0.35 + performance * 0.15, 2)
        return QualityReport("astro-splash-v1", score, 10, 9, visual, performance, evidence)


class AstroSplashImprover:
    """Only removes the known duplicate, cropped splash-image nodes."""

    def improve(self, source: str, issues: list[dict[str, object]]) -> Improvement | None:
        if not any(issue["issue"] == "duplicate_splash_assets" for issue in issues):
            return None
        removed = 0

        def remove_duplicate(match: re.Match[str]) -> str:
            nonlocal removed
            tag = match.group(0)
            is_duplicate = (
                "splash-bg" in tag
                and "object-cover" in tag
                and re.search(r'src="/images/封面图[23]\.jpg"', tag)
                and re.search(r'data-index="[12]"', tag)
            )
            if is_duplicate:
                removed += 1
                return ""
            return tag

        candidate = re.sub(r"<img\b[^>]*>", remove_duplicate, source)
        if removed != 2:
            return None
        return Improvement(
            "Remove duplicate cropped splash images",
            "The splash carousel has five image nodes for three assets; two duplicates reintroduce object-cover cropping.",
            candidate,
        )
