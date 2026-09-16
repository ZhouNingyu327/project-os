import tempfile
import unittest
from pathlib import Path

from project_os.changes import ChangeSet, ChangeSetError, FileChange, WorkspaceChangeApplier, sha256_text


class WorkspaceChangeApplierTest(unittest.TestCase):
    def test_applies_multiple_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "src" / "first.txt"
            first.parent.mkdir()
            first.write_text("before", encoding="utf-8")
            changes = ChangeSet(
                id="change-1",
                summary="Update two files",
                changes=(
                    FileChange("src/first.txt", sha256_text("before"), "after"),
                    FileChange("src/second.txt", None, "new"),
                ),
            )
            applied = WorkspaceChangeApplier(root).apply(changes)
            self.assertEqual([path.relative_to(root).as_posix() for path in applied], ["src/first.txt", "src/second.txt"])
            self.assertEqual(first.read_text(encoding="utf-8"), "after")
            self.assertEqual((root / "src" / "second.txt").read_text(encoding="utf-8"), "new")

    def test_rejects_path_escape_and_stale_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "safe.txt"
            file.write_text("current", encoding="utf-8")
            applier = WorkspaceChangeApplier(root)
            with self.assertRaises(ChangeSetError):
                applier.apply(ChangeSet("escape", "bad", (FileChange("../outside.txt", None, "bad"),)))
            with self.assertRaises(ChangeSetError):
                applier.apply(ChangeSet("stale", "bad", (FileChange("safe.txt", sha256_text("old"), "bad"),)))
