from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """Ephemeral execution context. Durable facts belong in SQLite."""

    project_id: int
    site_path: str
    project_path: str
    run_id: str
    baseline_version_id: int
    candidate_version_id: int
    task_id: int
    observation_id: int
    baseline_quality_id: int
    candidate_quality_id: int
    proposed_change: str
    outcome: str
    reason: str
    dry_run: bool
    validation_passed: bool
    validation_reason: str
    evaluation_findings: list[dict[str, object]]
    improvement_brief: dict[str, object]
