from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteProfile:
    """Stable project intent, kept outside short-lived graph state."""

    name: str
    goal: str
    constraints: tuple[str, ...]


SHAN_YICHUN_FANSITE = SiteProfile(
    name="Shan Yichun fansite",
    goal="Maintain an accurate, accessible and distinctive Chinese-language information site for Shan Yichun fans and new visitors.",
    constraints=(
        "Preserve source attribution and do not invent biographical or news claims.",
        "Keep the site usable on mobile devices.",
        "Respect the existing dark, editorial visual direction.",
        "Do not publish a candidate until it passes independent verification.",
    ),
)
