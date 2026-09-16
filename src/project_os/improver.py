from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Improvement:
    title: str
    rationale: str
    content: str


class DeterministicHtmlImprover:
    """A narrow safe tool: one known repair per run, no free-form code execution."""

    def improve(self, html: str, issues: list[dict[str, object]]) -> Improvement | None:
        names = {str(issue["issue"]) for issue in issues}
        if "missing_meta_description" in names and re.search(r"</head\s*>", html, re.I):
            content = re.sub(
                r"</head\s*>",
                '  <meta name="description" content="Personal website.">\n</head>',
                html,
                count=1,
                flags=re.I,
            )
            return Improvement("Add a page description", "Missing metadata reduced correctness score.", content)
        if "unlabeled_navigation" in names:
            content, changed = re.subn(r"<nav(\s|>)", r'<nav aria-label="Primary"\1', html, count=1, flags=re.I)
            if changed:
                return Improvement("Label primary navigation", "Navigation needs an accessible name.", content)
        if "missing_main_landmark" in names and re.search(r"</body\s*>", html, re.I):
            body = re.sub(r"<body([^>]*)>", r"<body\1><main>", html, count=1, flags=re.I)
            content = re.sub(r"</body\s*>", "</main></body>", body, count=1, flags=re.I)
            return Improvement("Add main landmark", "Main content needs a semantic landmark.", content)
        return None
