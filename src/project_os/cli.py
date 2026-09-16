from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import Database
from .workflow import EvolutionAgent
from .profiles import SHAN_YICHUN_FANSITE
from .fansite_profile import FansiteEvaluator, FansiteImprover
from .simulation import evolve_fansite_preview
from .verification import GitWorktreeBuildVerifier
from .research import NewsProposal, ResearchPipeline, SourceRegistry, stage_draft


DEMO_HTML = """<!doctype html>
<html lang="en"><head><title>Ada's portfolio</title></head>
<body><nav><a href="#work">Work</a></nav><h1>Ada Lovelace</h1><p>I design analytical tools.</p></body></html>
"""


def default_db(site_path: Path) -> Path:
    return site_path.parent / ".project-os" / "project-os.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Project OS V0.1 Evolution Agent.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-demo", help="Create an intentionally incomplete demo site.")
    run = subparsers.add_parser("run", help="Run one audited improvement cycle.")
    run.add_argument("--site", required=True, type=Path)
    run.add_argument("--db", type=Path)
    run.add_argument("--profile", choices=["shan-yichun-splash"])
    run.add_argument("--dry-run", action="store_true", help="Store and score a candidate without modifying the source file.")
    run.add_argument("--verify-worktree", action="store_true", help="Build a candidate in an isolated Git worktree before allowing a commit.")
    run.add_argument("--target-url", help="Deployed URL used only for opt-in runtime observations.")
    run.add_argument("--runtime-tools", action="store_true", help="Collect Lighthouse and browser screenshot observations for --target-url.")
    history = subparsers.add_parser("history", help="Show commit/reject decisions.")
    history.add_argument("--db", type=Path, default=Path("demo_site/.project-os/project-os.db"))
    evolve = subparsers.add_parser("evolve-fansite-preview", help="Safely run multiple fansite improvements in a throwaway workspace.")
    evolve.add_argument("--site", required=True, type=Path)
    evolve.add_argument("--db", required=True, type=Path)
    evolve.add_argument("--cycles", type=int, default=3)
    evolve.add_argument("--target-url", help="Deployed URL used only for opt-in runtime observations.")
    evolve.add_argument("--runtime-tools", action="store_true", help="Collect runtime observations for --target-url.")
    research = subparsers.add_parser("research-news", help="Search, cross-check and stage a proposed news item.")
    research.add_argument("--site", required=True, type=Path, help="Any source file inside the target website project.")
    research.add_argument("--db", required=True, type=Path)
    research.add_argument("--proposal", required=True, type=Path, help="JSON: title, publishDate, summary, tags, query, requiredTerms.")
    research.add_argument("--source-registry", required=True, type=Path)
    research.add_argument("--draft-dir", required=True, type=Path)
    research.add_argument("--publish-to", type=Path, help="Website news directory; only used when verification succeeds.")
    args = parser.parse_args()

    if getattr(args, "runtime_tools", False) and not args.target_url:
        parser.error("--runtime-tools requires --target-url")

    if args.command == "init-demo":
        target = Path("demo_site/index.html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DEMO_HTML, encoding="utf-8")
        print(f"Created {target}. Run: project-os run --site {target}")
        return
    if args.command == "history":
        db = Database(args.db)
        db.initialize()
        rows = db.history()
        if not rows:
            print("No decisions recorded.")
        for row in rows:
            print(f"{row['created_at']}  {row['decision'].upper():6}  {row['title']} — {row['reason']}")
        return
    if args.command == "evolve-fansite-preview":
        source = args.site.resolve()
        if not source.is_file():
            parser.error(f"Site source does not exist: {source}")
        db = Database(args.db)
        db.initialize()
        preview = evolve_fansite_preview(
            source, db, cycles=args.cycles, target_url=args.target_url, enable_runtime_tools=args.runtime_tools,
        )
        print(json.dumps({"initial": preview.initial, "final": preview.final, "steps": preview.steps}, ensure_ascii=False, indent=2))
        return
    if args.command == "research-news":
        site = args.site.resolve()
        if not site.is_file():
            parser.error(f"Site source does not exist: {site}")
        raw = json.loads(args.proposal.read_text(encoding="utf-8"))
        proposal = NewsProposal(
            title=raw["title"], publish_date=raw["publishDate"], summary=raw["summary"], tags=raw["tags"],
            query=raw["query"], required_terms=raw["requiredTerms"],
        )
        db = Database(args.db)
        db.initialize()
        project_id = db.create_or_get_project(site, name=SHAN_YICHUN_FANSITE.name, goal=SHAN_YICHUN_FANSITE.goal, constraints=list(SHAN_YICHUN_FANSITE.constraints))
        result = ResearchPipeline(db, SourceRegistry.from_json(args.source_registry)).research(project_id, proposal)
        destination = args.publish_to if args.publish_to and result.status == "verified" else args.draft_dir
        path = stage_draft(destination, proposal, result, publish=args.publish_to is not None)
        print(json.dumps({"status": result.status, "reason": result.reason, "evidence": [item.__dict__ for item in result.evidence], "output": str(path)}, ensure_ascii=False, indent=2))
        return
    site = args.site.resolve()
    if not site.is_file():
        parser.error(f"Site file does not exist: {site}")
    if args.profile == "shan-yichun-splash" and not args.dry_run and not args.verify_worktree:
        parser.error("The Shan Yichun profile requires --dry-run, or --verify-worktree for a verified live commit.")
    db = Database(args.db or default_db(site))
    db.initialize()
    if args.profile == "shan-yichun-splash":
        agent = EvolutionAgent(
            db, evaluator=FansiteEvaluator(site.parents[2], target_url=args.target_url, enable_runtime_tools=args.runtime_tools), improver=FansiteImprover(), profile=SHAN_YICHUN_FANSITE,
            candidate_verifier=GitWorktreeBuildVerifier() if args.verify_worktree else None,
        )
    else:
        agent = EvolutionAgent(db)
    result = agent.run(site, dry_run=args.dry_run)
    print(f"{result['outcome'].upper()}: {result.get('proposed_change', 'No change')}")
    print(result["reason"])


if __name__ == "__main__":
    main()
