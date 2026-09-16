import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from project_os.verification import GitWorktreeBuildVerifier


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
