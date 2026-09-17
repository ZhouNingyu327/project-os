"""Tool adapters that provide auditable inputs to read-only quality agents.

Static tools always run locally. Browser and Lighthouse tools are optional: an
unavailable runtime is reported as a limitation rather than silently converted
into a score.
"""

from __future__ import annotations

import json
import os
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

    @staticmethod
    def _normalise(value: object) -> str:
        """Make a conservative, formatting-insensitive identity comparison."""
        return re.sub(r"[^\w\u4e00-\u9fff]", "", str(value).casefold())

    def _stage_manifest_audit(self, root: Path, stage_directory: Path) -> tuple[dict[str, int | str], list[dict[str, object]], list[str]]:
        """Compare archive files with a project-owned expected-stage inventory.

        The manifest is deliberately data, not a hard-coded artist catalogue.
        Discovery sources (including social posts and OCR) can propose entries,
        but an entry is only covered when its matching archive file is verified
        and contains a source URL.  A researched negative result is permitted
        only when it records a rejection reason, so coverage cannot be inflated
        by silently dropping difficult candidates.
        """
        manifest = root / ".project-os" / "stage-manifest.json"
        empty = {"stage_manifest_entries": 0, "stage_manifest_covered": 0, "stage_manifest_rejected": 0, "stage_manifest_missing": 0, "stage_manifest_unverified": 0}
        if not manifest.exists():
            return {**empty, "stage_manifest_status": "not_configured"}, [{"issue": "stage_manifest_not_configured", "severity": 1}], ("No expected-stage manifest is configured; archive completeness cannot be claimed.",)
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            entries = payload["stages"]
            if not isinstance(entries, list):
                raise ValueError("'stages' must be an array")
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
            return {**empty, "stage_manifest_status": "invalid"}, [{"issue": "stage_manifest_invalid", "severity": 3}], (f"Stage manifest could not be read: {error}",)

        covered = rejected = missing = unverified = 0
        findings: list[dict[str, object]] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                findings.append({"issue": "stage_manifest_invalid_entry", "severity": 3})
                continue
            identifier = entry["id"]
            if entry.get("resolution") == "rejected":
                if isinstance(entry.get("rejectionReason"), str) and entry["rejectionReason"].strip():
                    rejected += 1
                else:
                    findings.append({"issue": "stage_manifest_rejection_without_reason", "severity": 3, "id": identifier})
                    missing += 1
                continue
            path = stage_directory / f"{identifier}.mdx"
            if not path.is_file():
                missing += 1
                findings.append({"issue": "expected_stage_missing_from_archive", "severity": 3, "id": identifier})
                continue
            text = path.read_text(encoding="utf-8")
            required_identity = entry.get("identity", {})
            expected_tokens = required_identity.values() if isinstance(required_identity, dict) else ()
            identity_matches = all(self._normalise(token) in self._normalise(text) for token in expected_tokens if str(token).strip())
            is_verified = "verificationStatus: verified" in text and bool(re.search(r"^\s*(?:-\s*)?url:\s*['\"]?https?://", text, flags=re.MULTILINE))
            if is_verified and identity_matches:
                covered += 1
            else:
                unverified += 1
                findings.append({"issue": "expected_stage_not_verified", "severity": 2, "id": identifier, "identity_matches": identity_matches})
        metrics: dict[str, int | str] = {
            "stage_manifest_entries": len(entries), "stage_manifest_covered": covered,
            "stage_manifest_rejected": rejected, "stage_manifest_missing": missing,
            "stage_manifest_unverified": unverified, "stage_manifest_status": "available",
        }
        if missing or unverified:
            findings.append({"issue": "stage_manifest_coverage_incomplete", "severity": 3, "covered": covered + rejected, "total": len(entries), "missing": missing, "unverified": unverified})
        return metrics, findings, ("Manifest entries are resolved only by a verified matching archive record or a documented negative research result.",)

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
        stage_directory = root / "src" / "content" / "stages"
        stages = list(stage_directory.glob("*.mdx")) if stage_directory.exists() else []
        verified_stages = pending_stages = legacy_stages = 0
        for path in stages:
            text = path.read_text(encoding="utf-8")
            has_source = bool(re.search(r"^\s*(?:-\s*)?url:\s*['\"]?https?://", text, flags=re.MULTILINE))
            if "verificationStatus: verified" in text and has_source:
                verified_stages += 1
            elif has_source or "verificationStatus: needs_review" in text:
                pending_stages += 1
            else:
                legacy_stages += 1
        if legacy_stages:
            findings.append({"issue": "stage_archive_missing_sources", "severity": 2, "count": legacy_stages, "total": len(stages)})
        if pending_stages:
            findings.append({"issue": "stage_archive_needing_cross_check", "severity": 2, "count": pending_stages, "total": len(stages)})
        # Other factual collections have different frontmatter schemas, but the
        # provenance rule is universal. A platform link, event link, or sources
        # array is counted as a traceable source; unlinked records are a bounded
        # research backlog rather than silently trusted site copy.
        generic_collections = ("awards", "biography", "discography", "events")
        generic_total = generic_sourced = 0
        for collection in generic_collections:
            paths = list((root / "src" / "content" / collection).glob("*.mdx"))
            generic_total += len(paths)
            sourced = sum(bool(re.search(r"^\s*(?:-\s*)?(?:url|sourceUrl):\s*['\"]?https?://", path.read_text(encoding="utf-8"), flags=re.MULTILINE)) for path in paths)
            generic_sourced += sourced
            if len(paths) - sourced:
                findings.append({"issue": "factual_collection_missing_provenance", "severity": 2, "collection": collection, "covered": sourced, "total": len(paths)})
        manifest_metrics, manifest_findings, manifest_limitations = self._stage_manifest_audit(root, stage_directory)
        findings.extend(manifest_findings)
        return ToolReport(self.name, "available", {
            "public_news": len(public_news), "verified_news": verified, "pending_news": pending, "unsourced_news": unsourced, "evidence_points": points,
            "stages": len(stages), "verified_stages": verified_stages, "pending_stages": pending_stages, "legacy_stages": legacy_stages,
            "factual_records": generic_total, "factual_records_with_sources": generic_sourced, "factual_records_without_sources": generic_total - generic_sourced,
            **manifest_metrics,
        }, tuple(findings), ("Coverage is not a claim-level truth determination.", *manifest_limitations))


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
        npx = "npx.cmd" if os.name == "nt" else "npx"
        command = (npx, "--yes", "lighthouse", target_url, "--quiet", "--output=json", f"--output-path={output}")
        try:
            result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
        except FileNotFoundError:
            return ToolReport(self.name, "unavailable", {}, limitations=("Node.js/npx is not installed or is not on PATH.",))
        except subprocess.TimeoutExpired:
            return ToolReport(self.name, "failed", {}, limitations=("Lighthouse did not finish within 180 seconds.",))
        if result.returncode or not output.exists():
            detail = (result.stderr or result.stdout).strip()[-500:]
            return ToolReport(self.name, "failed", {}, limitations=(f"Lighthouse did not complete: {detail}",))
        try:
            payload: dict[str, Any] = json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return ToolReport(self.name, "failed", {}, limitations=(f"Lighthouse report could not be parsed: {error}",))
        categories = payload.get("categories", {})
        metrics = {name: round(float(category.get("score", 0)) * 10, 2) for name, category in categories.items()}
        return ToolReport(self.name, "available", metrics)


class PlaywrightScreenshotTool:
    name = "playwright-screenshot"

    @staticmethod
    def _browser_executable() -> str | None:
        """Prefer a user-configured Chrome, then common Windows Chrome locations.

        This makes the runtime evaluator useful when Playwright's own browser
        download is unavailable on a restricted network.
        """
        configured = os.environ.get("PROJECT_OS_CHROME_EXECUTABLE")
        candidates = [configured] if configured else []
        candidates.extend(
            [
                r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
                r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
                str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ]
        )
        return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)

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
                executable = self._browser_executable()
                browser = playwright.chromium.launch(executable_path=executable) if executable else playwright.chromium.launch()
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
