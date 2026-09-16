from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .agents import QualityAssessmentAgent, TargetedImprovementAgent
from .database import Database, now
from .evaluators import MockWebsiteEvaluator
from .improver import DeterministicHtmlImprover
from .models import AgentState
from .policy import DecisionPolicy
from .profiles import SiteProfile
from .research import NewsProposal, PendingNewsPlanner, ResearchPipeline
from .verification import GitWorktreeBuildVerifier


class SupervisorAgent:
    """Schedules observation, quality assessment, targeted improvement and policy.

    Worker agents can assess and propose; this supervisor alone persists the
    task lifecycle and decides whether a verified candidate may be applied.
    """

    def __init__(self, db: Database, evaluator: MockWebsiteEvaluator | None = None, improver: DeterministicHtmlImprover | None = None, profile: SiteProfile | None = None, candidate_verifier: GitWorktreeBuildVerifier | None = None, decision_policy: DecisionPolicy | None = None, assessment_agent: QualityAssessmentAgent | None = None, improvement_agent: TargetedImprovementAgent | None = None, research_pipeline: ResearchPipeline | None = None) -> None:
        self.db = db
        self.evaluator = evaluator or MockWebsiteEvaluator()
        self.improver = improver or DeterministicHtmlImprover()
        self.assessment_agent = assessment_agent or QualityAssessmentAgent(self.evaluator)
        self.improvement_agent = improvement_agent or TargetedImprovementAgent(self.improver)
        self.profile = profile
        self.research_pipeline = research_pipeline
        self.candidate_verifier = candidate_verifier
        self.decision_policy = decision_policy or DecisionPolicy(require_validation=False)
        graph = StateGraph(AgentState)
        graph.add_node("observe", self.observe)
        graph.add_node("evaluate", self.evaluate)
        graph.add_node("diagnose", self.diagnose)
        graph.add_node("search", self.search)
        graph.add_node("verify_evidence", self.verify_evidence)
        graph.add_node("improve", self.improve)
        graph.add_node("verify", self.verify)
        graph.add_node("decide", self.decide)
        graph.add_edge(START, "observe")
        graph.add_edge("observe", "evaluate")
        graph.add_edge("evaluate", "diagnose")
        graph.add_conditional_edges("diagnose", self.route_after_diagnose, {"improve": "improve", "research": "search", "end": END})
        graph.add_edge("search", "verify_evidence")
        graph.add_edge("verify_evidence", "decide")
        graph.add_edge("improve", "verify")
        graph.add_edge("verify", "decide")
        graph.add_edge("decide", END)
        self.graph = graph.compile()

    @staticmethod
    def _research_root(site_path: Path) -> Path:
        for parent in (site_path.parent, *site_path.parents):
            if (parent / "src" / "content" / "news").is_dir():
                return parent
        return site_path.parent

    @staticmethod
    def _proposal_state(proposal: NewsProposal) -> dict[str, object]:
        return {"title": proposal.title, "publish_date": proposal.publish_date, "summary": proposal.summary, "tags": proposal.tags, "query": proposal.query, "required_terms": proposal.required_terms}

    @staticmethod
    def _proposal_from_state(raw: dict[str, object]) -> NewsProposal:
        return NewsProposal(str(raw["title"]), str(raw["publish_date"]), str(raw["summary"]), list(raw["tags"]), str(raw["query"]), list(raw["required_terms"]))

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
        assessment = self.assessment_agent.assess(html)
        report = assessment.report.as_dict()
        quality_id = self.db.add_quality(state["project_id"], state["baseline_version_id"], report)
        return {"baseline_quality_id": quality_id, "evaluation_findings": list(assessment.findings)}

    def diagnose(self, state: AgentState) -> AgentState:
        findings = tuple(state.get("evaluation_findings", []))
        if not findings:
            return {"outcome": "no_action", "reason": "No V0.1 evaluator finding requires a repair."}
        top = findings[0]
        brief = self.improvement_agent.brief_for(top)
        self.db.add_observation(state["project_id"], "improvement_brief", brief.as_dict() | {"run_id": state["run_id"]})
        title = f"Repair {top['issue']}"
        task_id = self.db.insert(
            "tasks", project_id=state["project_id"], observation_id=state["observation_id"], title=title,
            priority=int(top["severity"]), status="in_progress", rationale=f"{top['dimension']} finding routed by {self.improvement_agent.name}: {brief.objective}", created_at=now(), completed_at=None,
        )
        return {"task_id": task_id, "improvement_brief": brief.as_dict()}

    def route_after_diagnose(self, state: AgentState) -> str:
        if state.get("outcome") == "no_action":
            return "end"
        return "research" if state.get("improvement_brief", {}).get("requires_external_evidence") else "improve"

    def search(self, state: AgentState) -> AgentState:
        """Run discovery only; candidates remain untrusted until next node."""
        if self.research_pipeline is None:
            return {"outcome": "needs_research", "reason": "A source registry and search provider must be configured before this research task can run."}
        proposal = PendingNewsPlanner(self._research_root(Path(state["site_path"]))).next_proposal()
        if proposal is None:
            return {"outcome": "needs_research", "reason": "No explicitly pending news entry supplied a concrete claim for safe research."}
        try:
            claim_id = self.research_pipeline.start_claim(state["project_id"], proposal)
            discovered = self.research_pipeline.discover(proposal)
            candidates = discovered.candidates
            self.db.add_observation(state["project_id"], "search_candidates", {"claim_id": claim_id, "query": proposal.query, "candidate_urls": [item["url"] for item in candidates], "run_id": state["run_id"]})
            return {"research_claim_id": claim_id, "research_proposal": self._proposal_state(proposal), "research_candidates": candidates}
        except Exception as error:
            self.db.insert("failures", project_id=state["project_id"], task_id=state.get("task_id"), stage="web_search", error=str(error), created_at=now())
            return {"outcome": "needs_research", "reason": f"Search agent could not complete: {error}"}

    def verify_evidence(self, state: AgentState) -> AgentState:
        """Independently verifies the candidates found by the search worker."""
        if not state.get("research_claim_id") or self.research_pipeline is None:
            return {}
        try:
            proposal = self._proposal_from_state(state["research_proposal"])
            result = self.research_pipeline.verify_candidates(proposal, state.get("research_candidates", []))
            self.research_pipeline.persist_result(state["project_id"], state["research_claim_id"], result)
            self.db.add_observation(state["project_id"], "evidence_verification", {"claim_id": state["research_claim_id"], "status": result.status, "evidence_urls": [item.url for item in result.evidence], "run_id": state["run_id"]})
            outcome = "research_verified" if result.status == "verified" else "needs_research"
            return {"research_status": result.status, "outcome": outcome, "reason": result.reason}
        except Exception as error:
            self.db.insert("failures", project_id=state["project_id"], task_id=state.get("task_id"), stage="evidence_verification", error=str(error), created_at=now())
            return {"outcome": "needs_research", "reason": f"Evidence verification could not complete: {error}"}

    def improve(self, state: AgentState) -> AgentState:
        if state.get("outcome") == "no_action":
            return {}
        html = Path(state["site_path"]).read_text(encoding="utf-8")
        proposal = self.improvement_agent.propose_local_repair(html, tuple(state.get("evaluation_findings", [])))
        if proposal is None:
            brief = state.get("improvement_brief", {})
            if brief.get("requires_external_evidence"):
                return {"outcome": "needs_research", "reason": "The targeted improvement agent requires verified external evidence; no unverified content was written."}
            return {"outcome": "no_action", "reason": "No safe V0.1 repair exists for the detected finding."}
        candidate_id = self.db.add_version(state["project_id"], "candidate", proposal.content, state["baseline_version_id"])
        return {"candidate_version_id": candidate_id, "proposed_change": proposal.title, "reason": proposal.rationale}

    def verify(self, state: AgentState) -> AgentState:
        if not state.get("candidate_version_id"):
            return {}
        # Read candidate from durable storage rather than carrying source code in graph state.
        with self.db.connect() as conn:
            row = conn.execute("SELECT content FROM versions WHERE id = ?", (state["candidate_version_id"],)).fetchone()
        report = self.assessment_agent.assess(str(row["content"])).report.as_dict()
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
                if state.get("outcome") == "research_verified":
                    self.db.update_task(state["task_id"], "completed")
                else:
                    self.db.update_task(state["task_id"], "open" if state.get("outcome") == "needs_research" else "rejected")
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


# V0.1 public name retained for callers; the implementation is now a
# supervisor coordinating explicit worker-agent boundaries.
EvolutionAgent = SupervisorAgent
