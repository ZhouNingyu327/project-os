import tempfile
import unittest
from pathlib import Path

from project_os.database import Database


class ChangeSetPersistenceTest(unittest.TestCase):
    def test_change_set_and_full_file_content_are_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "index.html"
            target.write_text("baseline", encoding="utf-8")
            db = Database(root / "state.db")
            db.initialize()
            project_id = db.create_or_get_project(target)
            change_set_id = db.add_change_set(
                project_id,
                external_id="run-42",
                summary="Add page description",
                files=[{"path": "index.html", "expected_sha256": "abc", "content": "candidate"}],
            )
            db.update_change_set_status(change_set_id, "validated")
            with db.connect() as conn:
                change_set = conn.execute("SELECT status, summary FROM change_sets WHERE id = ?", (change_set_id,)).fetchone()
                file = conn.execute("SELECT path, content FROM change_set_files WHERE change_set_id = ?", (change_set_id,)).fetchone()
            self.assertEqual(change_set["status"], "validated")
            self.assertEqual(change_set["summary"], "Add page description")
            self.assertEqual(file["path"], "index.html")
            self.assertEqual(file["content"], "candidate")
