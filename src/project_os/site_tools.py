"""Tool adapters that provide auditable inputs to read-only quality agents.

Static tools always run locally. Browser and Lighthouse tools are optional: an
unavailable runtime is reported as a limitation rather than silently converted
into a score.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolReport:
    tool: str
    status: str  # available | unavailable | failed
    metrics: dict[str, float | int | str]
    findings: tuple[dict[str, object], ...] = ()
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RepositoryInventoryTool:
    name = "repository-inventory"

    def collect(self, root: Path, _: str) -> ToolReport:
        source = root / "src"
        files = [path for path in source.rglob("*") if path.is_file()] if source.exists() else []
        pages = len(list((source / "pages").rglob("*.astro"))) if (source / "pages").exists() else 0
        content = len(list((source / "content").rglob("*.mdx"))) if (source / "content").exists() else 0
        return ToolReport(self.name, "available", {"source_files": len(files), "pages": pages, "content_entries": content})


class MarkupCorrectnessTool:
    name = "markup-correctness"

    def collect(self, root: Path, source: str) -> ToolReport:
        # Astro layouts often delegate document tags to a SEO component. Include
        # that bounded component scope before declaring metadata absent.
        seo = root / "src" / "components" / "seo"
        seo_source = "\n".join(path.read_text(encoding="utf-8") for path in seo.rglob("*.astro")) if seo.exists() else ""
        lower = (source + "\n" + seo_source).lower()
        metrics = {
            "has_title": int("<title" in lower),
            "has_description": int("name=\"description\"" in lower or "name='description'" in lower),
            "has_html_language": int("<html" in lower and "lang=" in lower),
        }
        findings = tuple(
            {"issue": name, "severity": 2}
            for name, present in (("missing_title_element", metrics["has_title"]), ("missing_description_metadata", metrics["has_description"]))
            if not present
        )
        return ToolReport(self.name, "available", metrics, findings, ("This validates markup presence, not the factual correctness of page claims.",))


class AccessibilitySourceTool:
    name = "accessibility-source"

    def collect(self, _: Path, source: str) -> ToolReport:
        images = re.findall(r"<img\b[^>]*>", source, flags=re.I)
        missing_alt = sum(" alt=" not in tag for tag in images)
        findings: list[dict[str, object]] = []
        if missing_alt:
            findings.append({"issue": "images_missing_alt", "severity": 2, "count": missing_alt})
        if "<main" not in source.lower():
            findings.append({"issue": "missing_main_landmark", "severity": 2})
        if "<nav" in source.lower() and "aria-label=" not in source.lower():
            findings.append({"issue": "unlabeled_navigation", "severity": 2})
        return ToolReport(self.name, "available", {"images": len(images), "images_missing_alt": missing_alt}, tuple(findings), ("Source analysis is not a keyboard or screen-reader test.",))


class ContentEvidenceTool:
    name = "content-evidence"

    def collect(self, root: Path, _: str) -> ToolReport:
        directory = root / "src" / "content" / "news"
        all_news = list(directory.glob("*.mdx")) if directory.exists() else []
        public_news = [path for path in all_news if "publicationStatus: 'review'" not in path.read_text(encoding="utf-8")]
        verified = pending = unsourced = 0
        points = 0.0
        for path in public_news:
            text = path.read_text(encoding="utf-8")
            sources = len(re.findall(r"^\s*(?:-\s*)?(?:sourceUrl|url):\s*['\"]?https?://", text, flags=re.MULTILINE))
            if "verificationStatus: 'verified'" in text and sources >= 2:
                verified += 1; points += 1.0
            elif sources:
                pending += 1; points += 0.45
            else:
                unsourced += 1
        findings: list[dict[str, object]] = []
        if unsourced:
            findings.append({"issue": "published_news_without_sources", "severity": 3, "count": unsourced})
        if pending:
            findings.append({"issue": "published_news_needing_cross_check", "severity": 2, "count": pending})
        return ToolReport(self.name, "available", {"public_news": len(public_news), "verified_news": verified, "pending_news": pending, "unsourced_news": unsourced, "evidence_points": points}, tuple(findings), ("Coverage is not a claim-level truth determination.",))


class AssetPerformanceTool:
    name = "asset-performance"

    def collect(self, root: Path, source: str) -> ToolReport:
        public = root / "public"
        assets = [path for path in public.rglob("*") if path.is_file()] if public.exists() else []
        total_bytes = sum(path.stat().st_size for path in assets)
        duplicate_splash = len(re.findall(r'<img\b(?=[^>]*\bsplash-bg\b)[^>]*\bsrc="([^"]+)"', source))
        findings: list[dict[str, object]] = []
        if total_bytes > 5 * 1024 * 1024:
            findings.append({"issue": "large_public_asset_budget", "severity": 2, "bytes": total_bytes})
        if duplicate_splash:
            sources = re.findall(r'<img\b(?=[^>]*\bsplash-bg\b)[^>]*\bsrc="([^"]+)"', source)
            if len(sources) != len(set(sources)):
                findings.append({"issue": "duplicate_splash_assets", "severity": 3})
        return ToolReport(self.name, "available", {"asset_count": len(assets), "public_bytes": total_bytes}, tuple(findings), ("No network transfer timing is measured by this static tool.",))


class DesignSystemTool:
    name = "design-system-inventory"

    def collect(self, root: Path, _: str) -> ToolReport:
        styles = root / "src" / "styles"
        css = "\n".join(path.read_text(encoding="utf-8") for path in styles.rglob("*.css")) if styles.exists() else ""
        tokens = len(re.findall(r"--[\w-]+\s*:", css))
        return ToolReport(self.name, "available", {"css_bytes": len(css.encode()), "design_tokens": tokens}, limitations=("Token inventory is not a screenshot-based aesthetic judgment.",))


class LighthouseTool:
    name = "lighthouse"

    def collect(self, target_url: str | None, output: Path) -> ToolReport:
        if not target_url:
            return ToolReport(self.name, "unavailable", {}, limitations=("No target URL configured.",))
        output.parent.mkdir(parents=True, exist_ok=True)
        command = ("npx", "--yes", "lighthouse", target_url, "--quiet", "--output=json", f"--output-path={output}")
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
        if result.returncode or not output.exists():
            detail = (result.stderr or result.stdout).strip()[-500:]
            return ToolReport(self.name, "failed", {}, limitations=(f"Lighthouse did not complete: {detail}",))
        payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
        categories = payload.get("categories", {})
        metrics = {name: round(float(category.get("score", 0)) * 10, 2) for name, category in categories.items()}
        return ToolReport(self.name, "available", metrics)


class PlaywrightScreenshotTool:
    name = "playwright-screenshot"

    def collect(self, target_url: str | None, output: Path) -> ToolReport:
        if not target_url:
            return ToolReport(self.name, "unavailable", {}, limitations=("No target URL configured.",))
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ToolReport(self.name, "unavailable", {}, limitations=("Install the browser extra and run playwright install chromium.",))
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.goto(target_url, wait_until="networkidle", timeout=60_000)
                page.screenshot(path=str(output), full_page=True)
                metrics = page.evaluate("""() => ({height: document.documentElement.scrollHeight, images: document.images.length})""")
                browser.close()
            return ToolReport(self.name, "available", {"screenshot": str(output), "page_height": int(metrics["height"]), "images": int(metrics["images"])}, limitations=("A screenshot artifact requires a visual model or human reviewer for aesthetic scoring.",))
        except Exception as error:
            return ToolReport(self.name, "failed", {}, limitations=(f"Browser capture failed: {error}",))


class WebsiteToolchain:
    """Collects static signals, then optionally enriches them with runtime tools."""

    def __init__(self, *, target_url: str | None = None, artifact_dir: Path | None = None, enable_runtime: bool = False) -> None:
        self.target_url = target_url
        self.artifact_dir = artifact_dir
        self.enable_runtime = enable_runtime

    def collect(self, root: Path, source: str) -> dict[str, ToolReport]:
        reports = {tool.name: tool.collect(root, source) for tool in (RepositoryInventoryTool(), MarkupCorrectnessTool(), AccessibilitySourceTool(), ContentEvidenceTool(), AssetPerformanceTool(), DesignSystemTool())}
        if self.enable_runtime:
            destination = self.artifact_dir or root / ".project-os" / "artifacts"
            reports[LighthouseTool.name] = LighthouseTool().collect(self.target_url, destination / "lighthouse.json")
            reports[PlaywrightScreenshotTool.name] = PlaywrightScreenshotTool().collect(self.target_url, destination / "desktop.png")
        return reports
