import unittest
import json
from pathlib import Path
import tempfile

from project_os.fansite_profile import FansiteEvaluator, FansiteImprover


LAYOUT = '''<div id="splash-screen"><img src="/images/封面图1.jpg" class="splash-bg" />
<img src="/images/封面图2.jpg" class="splash-bg" />
<img src="/images/封面图3.jpg" class="splash-bg" />
<img src="/images/封面图2.jpg" class="splash-bg object-cover" />
<img src="/images/封面图3.jpg" class="splash-bg object-cover" />只有音乐和真心最重要 sessionStorage.getItem('splashShown')</div>'''


class FansiteEvaluatorTest(unittest.TestCase):
    def test_two_independent_repairs_raise_score(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            news = root / "src" / "content" / "news"
            news.mkdir(parents=True)
            (news / "news.mdx").write_text("---\nsourceUrl: https://example.com\n---", encoding="utf-8")
            evaluator = FansiteEvaluator(root)
            improver = FansiteImprover()
            baseline = evaluator.evaluate(LAYOUT)
            first = improver.improve(LAYOUT, baseline.evidence)
            second_report = evaluator.evaluate(first.content)
            second = improver.improve(first.content, second_report.evidence)
            final = evaluator.evaluate(second.content)
            self.assertGreater(final.overall_score, baseline.overall_score)
            self.assertEqual(final.usability, 9.5)

    def test_structured_sources_and_review_items_are_scored_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            news = root / "src" / "content" / "news"
            news.mkdir(parents=True)
            (news / "verified.mdx").write_text(
                "---\nverificationStatus: 'verified'\nsources:\n  - url: 'https://one.example.com'\n  - url: 'https://two.example.com'\n---",
                encoding="utf-8",
            )
            (news / "pending.mdx").write_text(
                "---\nverificationStatus: 'needs_review'\nsources:\n  - url: 'https://one.example.com'\n---",
                encoding="utf-8",
            )
            (news / "draft.mdx").write_text(
                "---\npublicationStatus: 'review'\n---",
                encoding="utf-8",
            )

            report = FansiteEvaluator(root).evaluate(LAYOUT)

            self.assertEqual(report.evidence_score, 7.25)
            self.assertTrue(any(item["issue"] == "published_news_needing_cross_check" for item in report.evidence))
            self.assertFalse(any(item["issue"] == "published_news_without_sources" for item in report.evidence))

    def test_incomplete_manifest_caps_evidence_score_and_surfaces_a_research_gap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stages = root / "src" / "content" / "stages"
            stages.mkdir(parents=True)
            (root / ".project-os").mkdir()
            (stages / "covered.mdx").write_text("---\nshow: Example\nverificationStatus: verified\nsources:\n  - url: https://official.test\n---", encoding="utf-8")
            (root / ".project-os" / "stage-manifest.json").write_text(json.dumps({"stages": [
                {"id": "covered", "identity": {"show": "Example"}},
                {"id": "not-yet-found", "identity": {"show": "Example"}},
            ]}), encoding="utf-8")
            report = FansiteEvaluator(root).evaluate(LAYOUT)
            self.assertEqual(report.evidence_score, 5.0)
            self.assertTrue(any(item["issue"] == "stage_manifest_coverage_incomplete" for item in report.evidence))
