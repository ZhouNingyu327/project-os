import tempfile
import unittest
import json
from pathlib import Path

from project_os.site_tools import WebsiteToolchain


class WebsiteToolchainTest(unittest.TestCase):
    def test_static_tools_collect_reusable_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "content" / "news").mkdir(parents=True)
            (root / "src" / "content" / "stages").mkdir(parents=True)
            (root / "src" / "styles").mkdir(parents=True)
            (root / "src" / "pages").mkdir(parents=True)
            (root / "src" / "styles" / "global.css").write_text(":root { --brand: red; }", encoding="utf-8")
            (root / "src" / "pages" / "index.astro").write_text("<main />", encoding="utf-8")
            (root / "src" / "content" / "news" / "item.mdx").write_text("---\nverificationStatus: 'verified'\nsources:\n  - url: 'https://one.test'\n  - url: 'https://two.test'\n---", encoding="utf-8")
            (root / "src" / "content" / "stages" / "item.mdx").write_text("---\nverificationStatus: verified\nsources:\n  - url: 'https://official.test'\n---", encoding="utf-8")
            reports = WebsiteToolchain().collect(root, '<img class="splash-bg" src="/one.jpg" />')
            self.assertEqual(set(reports), {"repository-inventory", "markup-correctness", "accessibility-source", "content-evidence", "asset-performance", "design-system-inventory"})
            self.assertEqual(reports["content-evidence"].metrics["verified_news"], 1)
            self.assertEqual(reports["content-evidence"].metrics["verified_stages"], 1)
            self.assertEqual(reports["design-system-inventory"].metrics["design_tokens"], 1)
            self.assertEqual(reports["repository-inventory"].metrics["pages"], 1)

    def test_manifest_detects_missing_expected_stage_even_when_archive_has_no_legacy_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stages = root / "src" / "content" / "stages"
            stages.mkdir(parents=True)
            (root / ".project-os").mkdir()
            (stages / "existing.mdx").write_text("---\nshow: Example\nepisode: Episode 1\nsongs: [Song]\nverificationStatus: verified\nsources:\n  - url: https://official.test\n---", encoding="utf-8")
            (root / ".project-os" / "stage-manifest.json").write_text(json.dumps({"stages": [
                {"id": "existing", "identity": {"show": "Example", "episode": "Episode 1", "song": "Song"}},
                {"id": "missing", "identity": {"show": "Example", "episode": "Episode 2", "song": "Other Song"}},
            ]}), encoding="utf-8")
            report = WebsiteToolchain().collect(root, "")["content-evidence"]
            self.assertEqual(report.metrics["stage_manifest_covered"], 1)
            self.assertEqual(report.metrics["stage_manifest_missing"], 1)
            self.assertTrue(any(item["issue"] == "expected_stage_missing_from_archive" for item in report.findings))
