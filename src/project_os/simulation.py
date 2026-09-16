from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .fansite_profile import FansiteEvaluator, FansiteImprover
from .profiles import SHAN_YICHUN_FANSITE
from .workflow import EvolutionAgent


@dataclass(frozen=True)
class EvolutionPreview:
    initial: dict[str, object]
    final: dict[str, object]
    steps: list[dict[str, str]]


def evolve_fansite_preview(source_path: Path, database: Database, *, cycles: int = 3) -> EvolutionPreview:
    """Iterate safely in a throwaway file while keeping the real target read-only."""
    evaluator = FansiteEvaluator(source_path.parents[2])  # src/layouts/BaseLayout.astro -> project root
    initial = evaluator.evaluate(source_path.read_text(encoding="utf-8")).as_dict()
    steps: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="project-os-") as folder:
        candidate_path = Path(folder) / source_path.name
        shutil.copy2(source_path, candidate_path)
        agent = EvolutionAgent(database, evaluator=evaluator, improver=FansiteImprover(), profile=SHAN_YICHUN_FANSITE)
        for _ in range(cycles):
            result = agent.run(candidate_path, project_path=source_path)
            steps.append({"outcome": result["outcome"], "change": result.get("proposed_change", "No change"), "reason": result["reason"]})
            if result["outcome"] != "commit":
                break
        final = evaluator.evaluate(candidate_path.read_text(encoding="utf-8")).as_dict()
    return EvolutionPreview(initial=initial, final=final, steps=steps)
