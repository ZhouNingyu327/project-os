from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .database import Database, now
from .evaluators import MockWebsiteEvaluator
from .improver import DeterministicHtmlImprover
from .models import AgentState
from .policy import DecisionPolicy
from .profiles import SiteProfile
from .verification import GitWorktreeBuildVerifier


class EvolutionAgent:
    def __init__(self, db: Database, evaluator: MockWebsiteEvaluator | None = None, improver: DeterministicHtmlImprover | None = None, profile: SiteProfile | None = None, candidate_verifier: GitWorktreeBuildVerifier | None = None, decision_policy: DecisionPolicy | None = None) -> None:
        self.db = db
        self.evaluator = evaluator or MockWebsiteEvaluator()
        self.improver = improver or DeterministicHtmlImprover()
        self.profile = profile
        self.candidate_verifier = candidate_verifier
        self.decision_policy = decision_policy or DecisionPolicy(require_validation=False)
        graph = StateGraph(AgentState)
        graph.add_node("observe", self.observe)
        graph.add_node("evaluate", self.evaluate)
        graph.add_node("diagnose", self.diagnose)
        graph.add_node("improve", self.improve)
        graph.add_node("verify", self.verify)
        graph.add_node("decide", self.decide)
        graph.add_edge(START, "observe")
        graph.add_edge("observe", "evaluate")
        graph.add_edge("evaluate", "diagnose")
        graph.add_edge("diagnose", "improve")
        graph.add_edge("improve", "verify")
        graph.add_edge("verify", "decide")
        graph.add_edge("decide", END)
        self.graph = graph.compile()

    def run(self, site_path: Path, *, dry_run: bool = False, project_path: Path | None = None) -> AgentState:
        return self.graph.invoke({
            "site_path": str(site_path.resolve()),
            "project_path": str((project_path or site_path).resolve()),
            "run_id": str(uuid.uuid4()),
            "dry_run": dry_run,
        })

    def observe(self, state: AgentState) -> AgentState:
        path = Path(state["site_path"])
        html = path.read_text(encoding="utf-8")
        profile = self.profile
        project_id = self.db.create_or_get_project(
            Path(state["project_path"]),
            name=profile.name if profile else None,
            goal=profile.goal if profile else "",
            constraints=list(profile.constraints) if profile else [],
        )
        version_id = self.db.add_version(project_id, "baseline", html)
        observation_id = self.db.add_observation(project_id, "html_source", {"path": str(path), "project_path": state["project_path"], "bytes": len(html), "run_id": state["run_id"]})
        return {"project_id": project_id, "baseline_version_id": version_id, "observation_id": observation_id}

    def evaluate(self, state: AgentState) -> AgentState:
        html = Path(state["site_path"]).read_text(encoding="utf-8")
        report = self.evaluator.evaluate(html).as_dict()
        quality_id = self.db.add_quality(state["project_id"], state["baseline_version_id"], report)
        return {"baseline_quality_id": quality_id}

    def diagnose(self, state: AgentState) -> AgentState:
        html = Path(state["site_path"]).read_text(encoding="utf-8")
        report = self.evaluator.evaluate(html)
        if not report.evidence:
            return {"outcome": "no_action", "reason": "No V0.1 evaluator finding requires a repair."}
        top = max(report.evidence, key=lambda item: int(item["severity"]))
        title = f"Repair {top['issue']}"
        task_id = self.db.insert(
            "tasks", project_id=state["project_id"], observation_id=state["observation_id"], title=title,
            priority=int(top["severity"]), status="in_progress", rationale=f"{top['dimension']} finding from independent evaluator.", created_at=now(), completed_at=None,
        )
        return {"task_id": task_id}

    def improve(self, state: AgentState) -> AgentState:
        if state.get("outcome") == "no_action":
            return {}
        html = Path(state["site_path"]).read_text(encoding="utf-8")
        proposal = self.improver.improve(html, self.evaluator.evaluate(html).evidence)
        if proposal is None:
            return {"outcome": "no_action", "reason": "No safe V0.1 repair exists for the detected finding."}
        candidate_id = self.db.add_version(state["project_id"], "candidate", proposal.content, state["baseline_version_id"])
        return {"candidate_version_id": candidate_id, "proposed_change": proposal.title, "reason": proposal.rationale}

    def verify(self, state: AgentState) -> AgentState:
        if not state.get("candidate_version_id"):
            return {}
        # Read candidate from durable storage rather than carrying source code in graph state.
        with self.db.connect() as conn:
            row = conn.execute("SELECT content FROM versions WHERE id = ?", (state["candidate_version_id"],)).fetchone()
        report = self.evaluator.evaluate(str(row["content"])).as_dict()
        quality_id = self.db.add_quality(state["project_id"], state["candidate_version_id"], report)
        if self.candidate_verifier is None:
            return {"candidate_quality_id": quality_id, "validation_passed": True, "validation_reason": "No external build verifier configured."}
        validation = self.candidate_verifier.validate(Path(state["site_path"]), str(row["content"]))
        if not validation.passed:
            self.db.insert("failures", project_id=state["project_id"], task_id=state.get("task_id"), stage="candidate_build", error=validation.reason, created_at=now())
        return {"candidate_quality_id": quality_id, "validation_passed": validation.passed, "validation_reason": validation.reason}

    def decide(self, state: AgentState) -> AgentState:
        if not state.get("candidate_version_id"):
            if state.get("task_id"):
                self.db.update_task(state["task_id"], "rejected")
            return {"outcome": state.get("outcome", "rejected"), "reason": state.get("reason", "No candidate was produced.")}
        with self.db.connect() as conn:
            baseline = conn.execute("SELECT * FROM quality_reports WHERE id = ?", (state["baseline_quality_id"],)).fetchone()
            candidate = conn.execute("SELECT * FROM quality_reports WHERE id = ?", (state["candidate_quality_id"],)).fetchone()
            candidate_content = conn.execute("SELECT content FROM versions WHERE id = ?", (state["candidate_version_id"],)).fetchone()["content"]
        policy = self.decision_policy.evaluate(
            baseline, candidate, validation_passed=state.get("validation_passed")
        )
        accepted = policy.accepted
        experiment_id = self.db.insert(
            "experiments", project_id=state["project_id"], task_id=state["task_id"], baseline_version_id=state["baseline_version_id"],
            candidate_version_id=state["candidate_version_id"], status="running", created_at=now(), completed_at=None,
        )
        decision = "preview" if accepted and state.get("dry_run") else "commit" if accepted else "reject"
        reason = (
            f"Score {baseline['overall_score']} → {candidate['overall_score']}; correctness {baseline['correctness']} → {candidate['correctness']}; {state.get('validation_reason', '')}"
            if accepted else f"Candidate failed policy: {policy.reason}; {state.get('validation_reason', '')}"
        )
        if accepted and not state.get("dry_run"):
            target = Path(state["site_path"])
            backup = target.with_suffix(target.suffix + ".pre-project-os")
            shutil.copy2(target, backup)
            target.write_text(str(candidate_content), encoding="utf-8")
            with self.db.connect() as conn:
                conn.execute("UPDATE versions SET kind = 'committed' WHERE id = ?", (state["candidate_version_id"],))
        self.db.update_task(state["task_id"], "previewed" if decision == "preview" else "completed" if accepted else "rejected")
        self.db.update_experiment(experiment_id, "previewed" if decision == "preview" else "committed" if accepted else "rejected")
        self.db.insert("decisions", project_id=state["project_id"], task_id=state["task_id"], experiment_id=experiment_id, decision=decision, reason=reason, created_at=now())
        if decision == "preview":
            reason = f"Preview only; {reason} Source file was not changed."
        return {"outcome": decision, "reason": reason}
