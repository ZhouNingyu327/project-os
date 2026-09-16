import tempfile
import unittest
from pathlib import Path

from project_os.database import Database
from project_os.evaluators import QualityReport
from project_os.workflow import SupervisorAgent


class PendingEvidenceEvaluator:
    def evaluate(self, _: str) -> QualityReport:
        finding = {"issue": "published_news_needing_cross_check", "dimension": "evidence", "severity": 2}
        return QualityReport("test", 7, 9, 8, 8, 8, [finding], evidence_score=5, originality=8, consistency=8)


class SupervisorAgentTest(unittest.TestCase):
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
