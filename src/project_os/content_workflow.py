"""Durable rules for research-backed website content.

This module is intentionally content-type neutral: stages, releases, news,
events and biographies share the same discovery-to-publication safeguards.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from .database import Database


class CandidateStatus(StrEnum):
    DISCOVERED = "discovered"
    NEEDS_REVIEW = "needs_review"
    VERIFIED = "verified"
    PUBLISHED = "published"
    REJECTED = "rejected"


class SourceRole(StrEnum):
    """Discovery leads may create candidates; only verification can support claims."""
    DISCOVERY = "discovery"
    VERIFICATION = "verification"


@dataclass(frozen=True)
class ContentIdentity:
    """Stable identity prevents duplicate counting under different filenames."""
    scope: str
    fields: tuple[tuple[str, str], ...]

    @property
    def key(self) -> str:
        canonical = "|".join((self.scope, *(f"{name}={value.strip().casefold()}" for name, value in sorted(self.fields))))
        return hashlib.sha256(canonical.encode()).hexdigest()


class ContentWorkflow:
    """A small state machine used by research workers and publication gates.

    A social post, screenshot, OCR result, or unregistered URL is a discovery
    lead only. It can create a durable candidate but cannot transition that
    candidate to verified or published.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def discover(self, project_id: int, identity: ContentIdentity, urls: list[str]) -> int:
        return self.database.upsert_content_candidate(
            project_id, identity_key=identity.key, scope=identity.scope,
            status=CandidateStatus.DISCOVERED, discovery_sources=urls,
        )

    def verify(self, project_id: int, identity: ContentIdentity, sources: list[dict[str, str]]) -> int:
        if len({source.get("domain", "") for source in sources if source.get("domain")}) < 2:
            return self.database.upsert_content_candidate(
                project_id, identity_key=identity.key, scope=identity.scope,
                status=CandidateStatus.NEEDS_REVIEW, verification=sources,
            )
        return self.database.upsert_content_candidate(
            project_id, identity_key=identity.key, scope=identity.scope,
            status=CandidateStatus.VERIFIED, verification=sources,
        )

    def reject(self, project_id: int, identity: ContentIdentity, reason: str) -> int:
        return self.database.upsert_content_candidate(
            project_id, identity_key=identity.key, scope=identity.scope,
            status=CandidateStatus.REJECTED, rejection_reason=reason,
        )
