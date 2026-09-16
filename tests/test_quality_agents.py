import tempfile
import unittest
from pathlib import Path

from project_os.database import Database
from project_os.fansite_profile import FansiteEvaluator


SOURCE = '<img src="/one.jpg" class="splash-bg" /><img src="/one.jpg" class="splash-bg" />'


class MultiAgentQualityTest(unittest.TestCase):
    def test_seven_specialists_return_auditable_assessments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "content" / "news").mkdir(parents=True)
            report = FansiteEvaluator(root).evaluate(SOURCE)
            self.assertEqual(len(report.assessments or []), 7)
            self.assertEqual({item["dimension"] for item in report.assessments or []}, {
                "correctness", "evidence_score", "usability", "visual", "originality", "performance", "consistency",
            })
            self.assertTrue(all("agent" in item for item in report.evidence))

    def test_agent_assessments_are_persisted_with_quality_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "content" / "news").mkdir(parents=True)
            target = root / "layout.astro"
            target.write_text(SOURCE, encoding="utf-8")
            db = Database(root / "state.db")
            db.initialize()
            project_id = db.create_or_get_project(target)
            version_id = db.add_version(project_id, "baseline", SOURCE)
            quality_id = db.add_quality(project_id, version_id, FansiteEvaluator(root).evaluate(SOURCE).as_dict())
            with db.connect() as conn:
                count = conn.execute("SELECT COUNT(*) AS count FROM agent_assessments WHERE quality_report_id = ?", (quality_id,)).fetchone()["count"]
            self.assertEqual(count, 7)

    def test_performance_agent_flags_a_long_first_visit_splash(self) -> None:
        source = """
        <img src=\"/one.jpg\" class=\"splash-bg\" alt=\"\" />
        <script>
          sessionStorage.getItem('splashShown');
          var minShow = 6000;
        </script>
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "content" / "news").mkdir(parents=True)
            report = FansiteEvaluator(root).evaluate(source)
        performance = next(item for item in report.assessments or [] if item["dimension"] == "performance")
        self.assertEqual(performance["score"], 8.0)
        self.assertIn("long_first_visit_splash", {item["issue"] for item in performance["findings"]})
