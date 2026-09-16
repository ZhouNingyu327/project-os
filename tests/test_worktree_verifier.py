import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_os.verification import GitWorktreeBuildVerifier
from project_os.changes import ChangeSet, FileChange, sha256_text
from project_os.verification import GitWorktreeChangeSetVerifier


class GitWorktreeBuildVerifierTest(unittest.TestCase):
    def test_candidate_builds_in_worktree_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source = root / "layout.txt"
            source.write_text("baseline", encoding="utf-8")
            for command in (("git", "init"), ("git", "config", "user.email", "test@example.com"), ("git", "config", "user.name", "Test"), ("git", "add", "layout.txt"), ("git", "commit", "-m", "baseline")):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            verifier = GitWorktreeBuildVerifier((sys.executable, "-c", "from pathlib import Path; assert Path('layout.txt').read_text() == 'candidate'"))
            result = verifier.validate(source, "candidate")
            if not result.passed and "Could not create isolated candidate workspace" in result.reason:
                self.skipTest("The sandbox maps Git's physical path outside this test's writable view.")
            self.assertTrue(result.passed, result.reason)
            self.assertEqual(source.read_text(encoding="utf-8"), "baseline")

    def test_change_set_builds_all_files_without_touching_workspace(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            first, second = root / "first.txt", root / "second.txt"
            first.write_text("baseline", encoding="utf-8")
            second.write_text("unchanged", encoding="utf-8")
            for command in (("git", "init"), ("git", "config", "user.email", "test@example.com"), ("git", "config", "user.name", "Test"), ("git", "add", "."), ("git", "commit", "-m", "baseline")):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            changes = ChangeSet("all-files", "Change both", (
                FileChange("first.txt", sha256_text("baseline"), "candidate"),
                FileChange("second.txt", sha256_text("unchanged"), "candidate-two"),
            ))
            verifier = GitWorktreeChangeSetVerifier(((sys.executable, "-c", "from pathlib import Path; assert Path('first.txt').read_text() == 'candidate'; assert Path('second.txt').read_text() == 'candidate-two'"),))
            result = verifier.validate(root, changes)
            if not result.passed and "Could not create isolated candidate workspace" in result.reason:
                self.skipTest("The sandbox maps Git's physical path outside this test's writable view.")
            self.assertTrue(result.passed, result.reason)
            self.assertEqual(first.read_text(encoding="utf-8"), "baseline")
            self.assertEqual(second.read_text(encoding="utf-8"), "unchanged")
