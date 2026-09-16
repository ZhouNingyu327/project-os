"""Safe, auditable application of a project-level change set."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ChangeSetError(RuntimeError):
    """Raised when a change set is unsafe or no longer matches its baseline."""


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FileChange:
    """A full replacement with an optimistic-concurrency precondition."""

    path: str
    expected_sha256: str | None
    content: str


@dataclass(frozen=True)
class ChangeSet:
    id: str
    summary: str
    changes: tuple[FileChange, ...]


class WorkspaceChangeApplier:
    """Applies a reviewed change set only inside one resolved workspace root.

    Paths are checked before any write happens. Existing files must match the
    expected digest, preventing an agent from overwriting a user's concurrent
    edit. Each write uses ``os.replace`` so a partial file is never exposed.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _target(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise ChangeSetError(f"Change path escapes workspace root: {relative_path}") from error
        return candidate

    def validate(self, change_set: ChangeSet) -> tuple[Path, ...]:
        if not change_set.changes:
            raise ChangeSetError("A change set must contain at least one file change.")
        seen: set[Path] = set()
        targets: list[Path] = []
        for change in change_set.changes:
            target = self._target(change.path)
            if target in seen:
                raise ChangeSetError(f"Change set contains duplicate path: {change.path}")
            seen.add(target)
            if change.expected_sha256 is None:
                if target.exists():
                    raise ChangeSetError(f"Expected new file but target already exists: {change.path}")
            else:
                if not target.is_file():
                    raise ChangeSetError(f"Expected existing file is missing: {change.path}")
                actual = sha256_text(target.read_text(encoding="utf-8"))
                if actual != change.expected_sha256:
                    raise ChangeSetError(f"Baseline digest changed for: {change.path}")
            targets.append(target)
        return tuple(targets)

    def apply(self, change_set: ChangeSet) -> tuple[Path, ...]:
        targets = self.validate(change_set)
        for target, change in zip(targets, change_set.changes, strict=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".project-os-", dir=target.parent, text=True)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                    handle.write(change.content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
            except Exception:
                Path(temporary_name).unlink(missing_ok=True)
                raise
        return targets
