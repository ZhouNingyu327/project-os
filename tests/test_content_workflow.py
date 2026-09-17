import tempfile
import unittest
from pathlib import Path

from project_os.content_workflow import ContentIdentity, ContentWorkflow
from project_os.database import Database


class ContentWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "agent.db")
        self.db.initialize()
        self.project_id = self.db.create_or_get_project(Path(self.directory.name) / "site")
        self.workflow = ContentWorkflow(self.db)
        self.identity = ContentIdentity("stage", (("show", "Example"), ("episode", "1"), ("song", "Song"), ("partner", "Guest")))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_discovery_is_persisted_but_is_not_verification(self) -> None:
        self.workflow.discover(self.project_id, self.identity, ["https://social.example/post"])
        self.workflow.verify(self.project_id, self.identity, [{"domain": "official.example", "url": "https://official.example/a"}])
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM content_candidates").fetchone()
        self.assertEqual(row["status"], "needs_review")

    def test_two_independent_sources_verify_and_rejection_requires_reason(self) -> None:
        self.workflow.verify(self.project_id, self.identity, [
            {"domain": "official.example", "url": "https://official.example/a"},
            {"domain": "broadcaster.example", "url": "https://broadcaster.example/b"},
        ])
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM content_candidates").fetchone()["status"], "verified")
        with self.assertRaises(ValueError):
            self.workflow.reject(self.project_id, self.identity, "")
