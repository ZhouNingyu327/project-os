import json
import tempfile
import unittest
from pathlib import Path

from project_os.database import Database
from project_os.research import EvidenceVerifier, NewsProposal, ResearchPipeline, SearchRequest, SourceRegistry, WebSearchAgent


class EvidenceVerifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = NewsProposal("艺人发布新作", "2026-09-16", "摘要", ["音乐"], "query", ["艺人", "新作"])
        self.verifier = EvidenceVerifier(SourceRegistry({
            "official.example": {"reliability": 0.95, "kind": "official"},
            "broadcast.example": {"reliability": 0.9, "kind": "broadcaster"},
        }))

    def test_requires_two_independent_high_trust_sources(self) -> None:
        result = self.verifier.verify(self.proposal, [
            {"url": "https://official.example/a", "title": "艺人新作", "excerpt": "艺人发布新作"},
            {"url": "https://broadcast.example/b", "title": "艺人新作", "excerpt": "艺人新作上线"},
        ])
        self.assertEqual(result.status, "verified")

    def test_single_source_is_held_for_review(self) -> None:
        result = self.verifier.verify(self.proposal, [{"url": "https://official.example/a", "title": "艺人新作", "excerpt": "艺人发布新作"}])
        self.assertEqual(result.status, "needs_review")

    def test_search_agent_deduplicates_bounded_candidate_leads(self) -> None:
        class FakeSearch:
            def search(self, query: str, limit: int) -> list[dict[str, str]]:
                self.query, self.limit = query, limit
                return [
                    {"url": "https://official.example/a", "title": "A", "excerpt": "one"},
                    {"url": "https://official.example/a", "title": "Duplicate", "excerpt": "two"},
                ]

        provider = FakeSearch()
        result = WebSearchAgent(provider).discover(SearchRequest("新作", "艺人 新作 官方", ["艺人", "新作"], limit=20))
        self.assertEqual(provider.limit, 12)
        self.assertEqual(len(result.candidates), 1)

    def test_pipeline_keeps_search_and_verification_as_separate_workers(self) -> None:
        class FakeSearch:
            def search(self, _: str, limit: int) -> list[dict[str, str]]:
                return [
                    {"url": "https://official.example/a", "title": "艺人新作", "excerpt": "lead"},
                    {"url": "https://broadcast.example/b", "title": "艺人新作", "excerpt": "lead"},
                ][:limit]

        class FakeFetcher:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def fetch_text(self, url: str) -> str:
                self.urls.append(url)
                return "艺人新作上线"

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "agent.db")
            db.initialize()
            project_id = db.create_or_get_project(Path(directory) / "site")
            fetcher = FakeFetcher()
            pipeline = ResearchPipeline(db, self.verifier.registry, search=FakeSearch(), fetcher=fetcher)
            result = pipeline.research(project_id, self.proposal)
            with db.connect() as conn:
                candidate = conn.execute("SELECT status, discovery_sources_json, verification_json FROM content_candidates").fetchone()
        self.assertEqual(result.status, "verified")
        self.assertEqual(len(fetcher.urls), 2)
        self.assertEqual(candidate["status"], "verified")
        self.assertEqual(len(json.loads(candidate["discovery_sources_json"])), 2)
        self.assertEqual(len(json.loads(candidate["verification_json"])), 2)
