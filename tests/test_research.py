import unittest

from project_os.research import EvidenceVerifier, NewsProposal, SourceRegistry


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
