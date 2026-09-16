from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    site_path TEXT NOT NULL UNIQUE,
    goal TEXT NOT NULL DEFAULT '',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quality_reports (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    version_id INTEGER REFERENCES versions(id),
    evaluator TEXT NOT NULL,
    overall_score REAL NOT NULL,
    correctness REAL NOT NULL,
    usability REAL NOT NULL,
    visual REAL NOT NULL,
    performance REAL NOT NULL,
    evidence_score REAL NOT NULL DEFAULT 0,
    originality REAL NOT NULL DEFAULT 0,
    consistency REAL NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_assessments (
    id INTEGER PRIMARY KEY,
    quality_report_id INTEGER NOT NULL REFERENCES quality_reports(id) ON DELETE CASCADE,
    agent TEXT NOT NULL,
    dimension TEXT NOT NULL,
    score REAL NOT NULL,
    confidence REAL NOT NULL,
    findings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    observation_id INTEGER REFERENCES observations(id),
    title TEXT NOT NULL,
    priority INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'in_progress', 'completed', 'rejected', 'previewed')),
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    parent_id INTEGER REFERENCES versions(id),
    kind TEXT NOT NULL CHECK(kind IN ('baseline', 'candidate', 'committed')),
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    claim_id INTEGER REFERENCES claims(id),
    source TEXT NOT NULL,
    excerpt TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    task_id INTEGER REFERENCES tasks(id),
    baseline_version_id INTEGER REFERENCES versions(id),
    candidate_version_id INTEGER REFERENCES versions(id),
    status TEXT NOT NULL CHECK(status IN ('running', 'committed', 'rejected', 'previewed')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    task_id INTEGER REFERENCES tasks(id),
    stage TEXT NOT NULL,
    error TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    task_id INTEGER REFERENCES tasks(id),
    experiment_id INTEGER REFERENCES experiments(id),
    decision TEXT NOT NULL CHECK(decision IN ('commit', 'reject', 'preview')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_sets (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    external_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('proposed', 'validated', 'applied', 'rejected')),
    created_at TEXT NOT NULL,
    UNIQUE(project_id, external_id)
);
CREATE TABLE IF NOT EXISTS change_set_files (
    id INTEGER PRIMARY KEY,
    change_set_id INTEGER NOT NULL REFERENCES change_sets(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    expected_sha256 TEXT,
    content_sha256 TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(change_set_id, path)
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
            if "goal" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN goal TEXT NOT NULL DEFAULT ''")
            if "constraints_json" not in columns:
                conn.execute("ALTER TABLE projects ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]'")
            quality_columns = {row["name"] for row in conn.execute("PRAGMA table_info(quality_reports)")}
            for column in ("evidence_score", "originality", "consistency"):
                if column not in quality_columns:
                    conn.execute(f"ALTER TABLE quality_reports ADD COLUMN {column} REAL NOT NULL DEFAULT 0")

    def create_or_get_project(self, site_path: Path, *, name: str | None = None, goal: str = "", constraints: list[str] | None = None) -> int:
        site_path = site_path.resolve()
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM projects WHERE site_path = ?", (str(site_path),)).fetchone()
            if row:
                return int(row["id"])
            cursor = conn.execute(
                "INSERT INTO projects(name, site_path, goal, constraints_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (name or site_path.parent.name or site_path.stem, str(site_path), goal, json.dumps(constraints or []), now()),
            )
            return int(cursor.lastrowid)

    def insert(self, table: str, **values: Any) -> int:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.connect() as conn:
            cursor = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))
            return int(cursor.lastrowid)

    def update_task(self, task_id: int, status: str) -> None:
        completed_at = now() if status in {"completed", "rejected"} else None
        with self.connect() as conn:
            conn.execute("UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?", (status, completed_at, task_id))

    def update_experiment(self, experiment_id: int, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE experiments SET status = ?, completed_at = ? WHERE id = ?",
                (status, now(), experiment_id),
            )

    def add_version(self, project_id: int, kind: str, content: str, parent_id: int | None = None) -> int:
        return self.insert(
            "versions",
            project_id=project_id,
            parent_id=parent_id,
            kind=kind,
            content=content,
            content_sha256=hashlib.sha256(content.encode()).hexdigest(),
            created_at=now(),
        )

    def add_observation(self, project_id: int, kind: str, payload: dict[str, Any]) -> int:
        return self.insert("observations", project_id=project_id, kind=kind, payload_json=json.dumps(payload), created_at=now())

    def add_quality(self, project_id: int, version_id: int, report: dict[str, Any]) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO quality_reports(project_id, version_id, evaluator, overall_score, correctness, usability, visual, performance, evidence_score, originality, consistency, evidence_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, version_id, report["evaluator"], report["overall_score"], report["correctness"], report["usability"], report["visual"], report["performance"], report.get("evidence_score", 0), report.get("originality", 0), report.get("consistency", 0), json.dumps(report["evidence"]), now()),
            )
            quality_id = int(cursor.lastrowid)
            for assessment in report.get("assessments", []):
                conn.execute(
                    "INSERT INTO agent_assessments(quality_report_id, agent, dimension, score, confidence, findings_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (quality_id, assessment["agent"], assessment["dimension"], assessment["score"], assessment["confidence"], json.dumps(assessment["findings"])),
                )
            return quality_id

    def history(self, project_id: int | None = None) -> list[sqlite3.Row]:
        query = "SELECT d.decision, d.reason, d.created_at, p.name AS project, t.title FROM decisions d JOIN projects p ON p.id=d.project_id LEFT JOIN tasks t ON t.id=d.task_id"
        values: tuple[Any, ...] = ()
        if project_id is not None:
            query += " WHERE d.project_id = ?"
            values = (project_id,)
        query += " ORDER BY d.id"
        with self.connect() as conn:
            return conn.execute(query, values).fetchall()

    def add_change_set(self, project_id: int, *, external_id: str, summary: str, files: list[dict[str, str | None]]) -> int:
        """Persist a multi-file candidate before it enters verification."""
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO change_sets(project_id, external_id, summary, status, created_at) VALUES (?, ?, ?, 'proposed', ?)",
                (project_id, external_id, summary, now()),
            )
            change_set_id = int(cursor.lastrowid)
            for file in files:
                content = str(file["content"])
                conn.execute(
                    "INSERT INTO change_set_files(change_set_id, path, expected_sha256, content_sha256, content) VALUES (?, ?, ?, ?, ?)",
                    (change_set_id, file["path"], file.get("expected_sha256"), hashlib.sha256(content.encode()).hexdigest(), content),
                )
            return change_set_id

    def update_change_set_status(self, change_set_id: int, status: str) -> None:
        if status not in {"proposed", "validated", "applied", "rejected"}:
            raise ValueError(f"Unsupported change-set status: {status}")
        with self.connect() as conn:
            conn.execute("UPDATE change_sets SET status = ? WHERE id = ?", (status, change_set_id))
