from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    passed: bool
    reason: str


class GitWorktreeBuildVerifier:
    """Build a candidate in an isolated Git worktree before a live-file commit.

    The temporary worktree sits beneath ``.project-os/worktrees`` in the repository.
    Node's normal ancestor lookup can therefore reuse the existing repository's
    ``node_modules`` without copying dependencies into the candidate or mutating
    the checked-out source.
    """

    def __init__(self, build_command: tuple[str, ...] = ("npm", "run", "build")) -> None:
        self.build_command = build_command

    def validate(self, source_path: Path, candidate: str) -> ValidationResult:
        root_result = subprocess.run(
            ("git", "rev-parse", "--show-toplevel"), cwd=source_path.parent,
            text=True, capture_output=True, check=False,
        )
        if root_result.returncode:
            return ValidationResult(False, "Candidate verification requires a Git repository.")
        root = Path(root_result.stdout.strip())
        # Ask Git for the repository-relative directory instead of relying on
        # Windows path normalization (which can differ across drive-letter case).
        prefix_result = subprocess.run(
            ("git", "rev-parse", "--show-prefix"), cwd=source_path.parent,
            text=True, capture_output=True, check=False,
        )
        if prefix_result.returncode:
            return ValidationResult(False, "Could not resolve the candidate's repository-relative path.")
        relative_source = Path(prefix_result.stdout.strip()) / source_path.name

        holder = root / ".project-os" / "worktrees"
        try:
            holder.mkdir(parents=True, exist_ok=True)
            worktree = Path(tempfile.mkdtemp(prefix="candidate-", dir=holder))
        except OSError as error:
            return ValidationResult(False, f"Could not create isolated candidate workspace: {error}")
        worktree.rmdir()  # git worktree requires its target path not to exist
        added = False
        try:
            add = subprocess.run(
                ("git", "worktree", "add", "--detach", str(worktree), "HEAD"), cwd=root,
                text=True, capture_output=True, check=False,
            )
            if add.returncode:
                return ValidationResult(False, f"Could not create candidate worktree: {add.stderr.strip()}")
            added = True
            (worktree / relative_source).write_text(candidate, encoding="utf-8")
            build = subprocess.run(self.build_command, cwd=worktree, text=True, capture_output=True, check=False)
            if build.returncode:
                detail = (build.stderr or build.stdout).strip()[-1200:]
                return ValidationResult(False, f"Candidate build failed: {detail}")
            return ValidationResult(True, "Candidate built successfully in an isolated Git worktree.")
        finally:
            if added:
                subprocess.run(("git", "worktree", "remove", "--force", str(worktree)), cwd=root, text=True, capture_output=True, check=False)
            elif worktree.exists():
                shutil.rmtree(worktree, ignore_errors=True)
