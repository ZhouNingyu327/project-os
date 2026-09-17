import tempfile
import unittest
from pathlib import Path

from project_os.database import Database
from project_os.evaluators import QualityReport
from project_os.research import ResearchPipeline, SourceRegistry
from project_os.workflow import SupervisorAgent


class PendingEvidenceEvaluator:
    def evaluate(self, _: str) -> QualityReport:
        finding = {"issue": "published_news_needing_cross_check", "dimension": "evidence", "severity": 2}
        return QualityReport("test", 7, 9, 8, 8, 8, [finding], evidence_score=5, originality=8, consistency=8)


class SupervisorAgentTest(unittest.TestCase):
    def test_stage_manifest_gap_is_a_research_brief_not_a_local_content_edit(self) -> None:
        from project_os.agents import TargetedImprovementAgent
        from project_os.fansite_profile import FansiteImprover

        brief = TargetedImprovementAgent(FansiteImprover()).brief_for({"issue": "stage_manifest_coverage_incomplete", "dimension": "evidence", "severity": 3})
        self.assertTrue(brief.requires_external_evidence)
        self.assertIn("programme-inventory", brief.suggested_tools)
        self.assertIn("verify each unresolved stage identity", brief.objective)

    def test_supervisor_routes_evidence_gap_to_research_without_writing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "index.html"
            original = "<html><head><title>x</title></head><body>x</body></html>"
            site.write_text(original, encoding="utf-8")
            db = Database(root / "project-os.db")
            db.initialize()

            result = SupervisorAgent(db, evaluator=PendingEvidenceEvaluator()).run(site)

            self.assertEqual(result["outcome"], "needs_research")
            self.assertEqual(site.read_text(encoding="utf-8"), original)
            with db.connect() as conn:
                task = conn.execute("SELECT status FROM tasks").fetchone()
                brief = conn.execute("SELECT payload_json FROM observations WHERE kind = 'improvement_brief'").fetchone()
            self.assertEqual(task["status"], "open")
            self.assertIn("evidence-verifier", brief["payload_json"])

    def test_supervisor_executes_search_then_independent_evidence_verification(self) -> None:
        class FakeSearch:
            def search(self, _: str, limit: int) -> list[dict[str, str]]:
                return [
                    {"url": "https://official.example/a", "title": "官方公告", "excerpt": "lead"},
                    {"url": "https://news.example/b", "title": "媒体报道", "excerpt": "lead"},
                ][:limit]

        class FakeFetcher:
            def fetch_text(self, _: str) -> str:
                return "单依纯待核验新闻 官方公告"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site = root / "src" / "layouts" / "BaseLayout.astro"
            site.parent.mkdir(parents=True)
            site.write_text("<html><head><title>x</title></head><body>x</body></html>", encoding="utf-8")
            news = root / "src" / "content" / "news"
            news.mkdir(parents=True)
            (news / "pending.mdx").write_text(
                "---\ntitle: '单依纯待核验新闻'\npublishDate: 2026-01-01\nverificationStatus: 'needs_review'\nsummary: '等待官方公告核验。'\ntags: ['新闻']\n---\n",
                encoding="utf-8",
            )
            db = Database(root / "project-os.db")
            db.initialize()
            registry = SourceRegistry({
                "official.example": {"reliability": 0.95, "kind": "official"},
                "news.example": {"reliability": 0.9, "kind": "publisher"},
            })
            pipeline = ResearchPipeline(db, registry, search=FakeSearch(), fetcher=FakeFetcher())

            result = SupervisorAgent(db, evaluator=PendingEvidenceEvaluator(), research_pipeline=pipeline).run(site)

            self.assertEqual(result["outcome"], "research_verified")
            with db.connect() as conn:
                claim = conn.execute("SELECT status FROM claims").fetchone()
                evidence_count = conn.execute("SELECT COUNT(*) AS count FROM evidence").fetchone()["count"]
                task = conn.execute("SELECT status FROM tasks").fetchone()
                kinds = {row["kind"] for row in conn.execute("SELECT kind FROM observations")}
            self.assertEqual(claim["status"], "verified")
            self.assertEqual(evidence_count, 2)
            self.assertEqual(task["status"], "completed")
            self.assertTrue({"search_candidates", "evidence_verification"}.issubset(kinds))
