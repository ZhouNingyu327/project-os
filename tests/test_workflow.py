import tempfile
import unittest
from pathlib import Path

from project_os.database import Database
from project_os.evaluators import MockWebsiteEvaluator, QualityReport
from project_os.workflow import EvolutionAgent


class EvolutionAgentTest(unittest.TestCase):
    def test_improved_candidate_commits_and_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            site = tmp_path / "index.html"
            site.write_text("<html><head><title>x</title></head><body><nav>x</nav><h1>x</h1></body></html>", encoding="utf-8")
            db = Database(tmp_path / "project-os.db")
            db.initialize()
            result = EvolutionAgent(db).run(site)
            self.assertEqual(result["outcome"], "commit")
            self.assertIn("meta name=\"description\"", site.read_text(encoding="utf-8"))
            self.assertTrue(site.with_suffix(".html.pre-project-os").exists())
            self.assertEqual(db.history()[0]["decision"], "commit")

    def test_no_known_repair_does_not_change_site(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            site = tmp_path / "index.html"
            original = "<html><head><title>x</title></head><body><nav>x</nav><h1>x</h1></body></html>"
            site.write_text(original, encoding="utf-8")
            db = Database(tmp_path / "project-os.db")
            db.initialize()
            EvolutionAgent(db).run(site)  # repairs meta description
            EvolutionAgent(db).run(site)  # repairs nav
            EvolutionAgent(db).run(site)  # repairs main
            result = EvolutionAgent(db).run(site)  # styling needs a future tool
            self.assertEqual(result["outcome"], "no_action")
            self.assertNotEqual(site.read_text(encoding="utf-8"), original)

    def test_regressing_candidate_is_rejected_without_overwriting_site(self) -> None:
        class RegressingEvaluator(MockWebsiteEvaluator):
            def evaluate(self, html: str) -> QualityReport:
                if 'name="description"' in html:
                    return QualityReport("controlled-test", 5, 5, 5, 5, 5, [])
                return QualityReport("controlled-test", 8, 8, 8, 8, 8, [{"issue": "missing_meta_description", "dimension": "correctness", "severity": 2}])

        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            site = tmp_path / "index.html"
            original = "<html><head><title>x</title></head><body><nav>x</nav><h1>x</h1></body></html>"
            site.write_text(original, encoding="utf-8")
            db = Database(tmp_path / "project-os.db")
            db.initialize()
            result = EvolutionAgent(db, evaluator=RegressingEvaluator()).run(site)
            self.assertEqual(result["outcome"], "reject")
            self.assertEqual(site.read_text(encoding="utf-8"), original)
            self.assertEqual(db.history()[0]["decision"], "reject")
