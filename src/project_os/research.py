from __future__ import annotations

import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database, now


@dataclass(frozen=True)
class NewsProposal:
    title: str
    publish_date: str
    summary: str
    tags: list[str]
    query: str
    required_terms: list[str]


@dataclass(frozen=True)
class WebEvidence:
    url: str
    domain: str
    title: str
    excerpt: str
    retrieved_at: str
    reliability: float
    source_kind: str


@dataclass(frozen=True)
class VerificationResult:
    status: str  # verified | needs_review
    reason: str
    evidence: list[WebEvidence]


class SourceRegistry:
    """Explicit source-quality policy. Unknown domains are never high-trust."""

    def __init__(self, entries: dict[str, dict[str, Any]]) -> None:
        self.entries = {domain.lower(): value for domain, value in entries.items()}

    @classmethod
    def from_json(cls, path: Path) -> "SourceRegistry":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(raw["sources"])

    def score(self, domain: str) -> tuple[float, str]:
        entry = self.entries.get(domain.lower())
        if entry is None:
            return 0.25, "unregistered"
        return float(entry["reliability"]), str(entry["kind"])


class TavilySearch:
    """Optional web-search adapter; uses TAVILY_API_KEY only at execution time."""

    endpoint = "https://api.tavily.com/search"

    def search(self, query: str, limit: int = 6) -> list[dict[str, str]]:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is required for web research.")
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps({"api_key": api_key, "query": query, "max_results": limit, "search_depth": "advanced"}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "ProjectOS/0.3"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310: user explicitly configures research query
            payload = json.loads(response.read().decode("utf-8"))
        return [
            {"url": item["url"], "title": item.get("title", ""), "excerpt": item.get("content", "")}
            for item in payload.get("results", [])
        ]


class RespectfulPageFetcher:
    """Fetches public HTML only when the site's robots policy permits this agent."""

    def fetch_text(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute HTTP(S) URLs may be fetched.")
        robots = urllib.robotparser.RobotFileParser()
        robots.set_url(f"{parsed.scheme}://{parsed.netloc}/robots.txt")
        try:
            robots.read()
        except OSError:
            # A missing robots file is not permission to scrape aggressively: this
            # fetcher still makes one bounded request, never a crawl sweep.
            pass
        if robots.mtime() and not robots.can_fetch("ProjectOS", url):
            raise PermissionError("robots.txt disallows ProjectOS for this URL.")
        request = urllib.request.Request(url, headers={"User-Agent": "ProjectOS/0.3 (+research verification)"})
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310: URLs originate from configured search provider
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise ValueError(f"Unsupported evidence content type: {content_type}")
            body = response.read(1_000_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        body = re.sub(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", " ", body, flags=re.I | re.S)
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(body))).strip()


class EvidenceVerifier:
    """Verifies agreement mechanically; it never invents support from an LLM summary."""

    def __init__(self, registry: SourceRegistry) -> None:
        self.registry = registry

    def verify(self, proposal: NewsProposal, search_results: list[dict[str, str]]) -> VerificationResult:
        evidence: list[WebEvidence] = []
        required = [term.casefold() for term in proposal.required_terms]
        for item in search_results:
            parsed = urllib.parse.urlparse(item["url"])
            domain = (parsed.hostname or "").lower().removeprefix("www.")
            reliability, kind = self.registry.score(domain)
            text = f"{item.get('title', '')} {item.get('excerpt', '')}".casefold()
            term_coverage = sum(term in text for term in required) / len(required) if required else 1.0
            if reliability >= 0.8 and term_coverage >= 0.75:
                evidence.append(WebEvidence(item["url"], domain, item.get("title", ""), item.get("excerpt", "")[:800], datetime.now(UTC).isoformat(), reliability, kind))

        independent_domains = {item.domain for item in evidence}
        if len(independent_domains) >= 2:
            return VerificationResult("verified", "Two or more independent high-trust sources support the required terms.", evidence)
        return VerificationResult("needs_review", "Fewer than two independent high-trust sources support the required terms; no publication is allowed.", evidence)


class ResearchPipeline:
    def __init__(self, database: Database, registry: SourceRegistry, search: TavilySearch | None = None, fetcher: RespectfulPageFetcher | None = None) -> None:
        self.database = database
        self.verifier = EvidenceVerifier(registry)
        self.search = search or TavilySearch()
        self.fetcher = fetcher or RespectfulPageFetcher()

    def research(self, project_id: int, proposal: NewsProposal) -> VerificationResult:
        claim_id = self.database.insert("claims", project_id=project_id, text=proposal.title, status="researching", created_at=now())
        try:
            search_results = self.search.search(proposal.query)
            fetched_results = []
            for item in search_results:
                try:
                    fetched_results.append({**item, "excerpt": self.fetcher.fetch_text(item["url"])[:8_000]})
                except (OSError, PermissionError, ValueError, UnicodeError):
                    # An unavailable or disallowed page remains a discovery lead,
                    # never evidence supporting publication.
                    continue
            result = self.verifier.verify(proposal, fetched_results)
            for item in result.evidence:
                self.database.insert("evidence", project_id=project_id, claim_id=claim_id, source=item.url, excerpt=item.excerpt, created_at=now())
            with self.database.connect() as connection:
                connection.execute("UPDATE claims SET status = ? WHERE id = ?", (result.status, claim_id))
            return result
        except Exception as error:
            self.database.insert("failures", project_id=project_id, task_id=None, stage="web_research", error=str(error), created_at=now())
            with self.database.connect() as connection:
                connection.execute("UPDATE claims SET status = 'research_failed' WHERE id = ?", (claim_id,))
            raise


def draft_mdx(proposal: NewsProposal, verification: VerificationResult) -> str:
    """Produce a transparent news draft; caller controls whether it reaches the site."""
    sources = [{"name": item.title or item.domain, "url": item.url, "excerpt": item.excerpt} for item in verification.evidence]
    frontmatter = {
        "title": proposal.title,
        "publishDate": proposal.publish_date,
        "source": "Project OS research",
        "sources": sources,
        "verificationStatus": verification.status,
        "summary": proposal.summary,
        "tags": proposal.tags,
        "locale": "zh",
    }
    # JSON strings are valid YAML scalars and safely preserve quotes in titles/excerpts.
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()] + ["---", "", proposal.summary, ""]
    return "\n".join(lines)


def stage_draft(directory: Path, proposal: NewsProposal, verification: VerificationResult, *, publish: bool = False) -> Path:
    if publish and verification.status != "verified":
        raise ValueError("Only verified proposals may be published.")
    directory.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", proposal.title.casefold()).strip("-") or hashlib.sha256(proposal.title.encode()).hexdigest()[:12]
    target = directory / f"{proposal.publish_date}-{slug}.mdx"
    target.write_text(draft_mdx(proposal, verification), encoding="utf-8")
    return target
