import tempfile
import unittest
from pathlib import Path

from project_os.site_tools import WebsiteToolchain


class WebsiteToolchainTest(unittest.TestCase):
    def test_static_tools_collect_reusable_signals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src" / "content" / "news").mkdir(parents=True)
            (root / "src" / "styles").mkdir(parents=True)
            (root / "src" / "pages").mkdir(parents=True)
            (root / "src" / "styles" / "global.css").write_text(":root { --brand: red; }", encoding="utf-8")
            (root / "src" / "pages" / "index.astro").write_text("<main />", encoding="utf-8")
            (root / "src" / "content" / "news" / "item.mdx").write_text("---\nverificationStatus: 'verified'\nsources:\n  - url: 'https://one.test'\n  - url: 'https://two.test'\n---", encoding="utf-8")
            reports = WebsiteToolchain().collect(root, '<img class="splash-bg" src="/one.jpg" />')
            self.assertEqual(set(reports), {"repository-inventory", "markup-correctness", "accessibility-source", "content-evidence", "asset-performance", "design-system-inventory"})
            self.assertEqual(reports["content-evidence"].metrics["verified_news"], 1)
            self.assertEqual(reports["design-system-inventory"].metrics["design_tokens"], 1)
            self.assertEqual(reports["repository-inventory"].metrics["pages"], 1)
