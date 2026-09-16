from __future__ import annotations

import re
from pathlib import Path

from .evaluators import QualityReport
from .improver import Improvement


class FansiteEvaluator:
    """Independent, deterministic V0.3 evaluator for the real Astro fansite source.

    It scores verifiable source properties—not the truth of facts or rendered pixels.
    Those two limits are explicit so later browser and research evaluators can replace
    individual dimensions without weakening the commit policy.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def evaluate(self, layout: str) -> QualityReport:
        findings: list[dict[str, object]] = []
        splash_tags = re.findall(r"<img\b(?=[^>]*\bsplash-bg\b)[^>]*>", layout)
        sources = [match.group(1) for tag in splash_tags if (match := re.search(r'src="([^"]+)"', tag))]
        duplicates = sorted({source for source in sources if sources.count(source) > 1})

        correctness = 10.0
        usability = 9.5
        visual = 8.5
        originality = 8.0
        performance = 9.0
        consistency = 8.5

        if duplicates:
            visual -= 2.5
            performance -= 0.5
            findings.append({"issue": "duplicate_splash_assets", "dimension": "visual", "severity": 3, "duplicates": duplicates})
        missing_alt = sum(" alt=" not in tag for tag in splash_tags)
        if missing_alt:
            usability -= 1.5
            findings.append({"issue": "decorative_splash_images_need_empty_alt", "dimension": "usability", "severity": 2, "count": missing_alt})
        if "sessionStorage.getItem('splashShown')" not in layout:
            performance -= 1
            findings.append({"issue": "splash_repeats_each_navigation", "dimension": "performance", "severity": 2})
        if "只有音乐和真心最重要" not in layout:
            originality -= 1.5
            findings.append({"issue": "missing_project_brand_voice", "dimension": "originality", "severity": 2})

        all_news = list((self.project_root / "src" / "content" / "news").glob("*.mdx"))
        # Only public items should affect public-site quality. A `review` item is a
        # retained research record, not a published claim.
        news_files = [path for path in all_news if "publicationStatus: 'review'" not in path.read_text(encoding="utf-8")]
        verified = 0
        pending = 0
        unsourced = 0
        evidence_points = 0.0
        for path in news_files:
            text = path.read_text(encoding="utf-8")
            source_count = len(re.findall(r"^\s*(?:-\s*)?(?:sourceUrl|url):\s*['\"]?https?://", text, flags=re.MULTILINE))
            is_verified = "verificationStatus: 'verified'" in text
            if is_verified and source_count >= 2:
                verified += 1
                evidence_points += 1.0
            elif source_count:
                pending += 1
                # A linked, but not independently verified, claim gets partial
                # credit; it must never look equivalent to a cross-checked fact.
                evidence_points += 0.45
            else:
                unsourced += 1

        evidence_score = round(10 * evidence_points / len(news_files), 2) if news_files else 0.0
        if unsourced:
            findings.append({"issue": "published_news_without_sources", "dimension": "evidence", "severity": 3, "covered": len(news_files) - unsourced, "total": len(news_files)})
        if pending:
            findings.append({"issue": "published_news_needing_cross_check", "dimension": "evidence", "severity": 2, "pending": pending, "verified": verified, "total": len(news_files)})
        if not news_files:
            findings.append({"issue": "no_news_evidence_to_assess", "dimension": "evidence", "severity": 3})

        score = round(
            correctness * 0.20 + evidence_score * 0.18 + usability * 0.16 + visual * 0.18
            + originality * 0.10 + performance * 0.09 + consistency * 0.09,
            2,
        )
        return QualityReport(
            "fansite-source-v3", score, correctness, usability, visual, performance, findings,
            evidence_score=evidence_score, originality=originality, consistency=consistency,
        )


class FansiteImprover:
    """Produces one narrowly scoped, reversible repair per iteration."""

    def improve(self, source: str, issues: list[dict[str, object]]) -> Improvement | None:
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
