#!/usr/bin/env python3
"""Validate links in README.md and docs/*.md files.

Checks:
- GitHub Pages URLs return 200 (with --online flag)
- Relative file links resolve to existing files
- Anchor fragments match known player page IDs
- ARTEFACTS block structure is well-formed

Exit code 0 = all valid, 1 = failures found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VALID_ANCHORS = {"", "#video", "#infographic", "#slides"}
PAGES_URL_RE = re.compile(r"https://[\w.-]+\.github\.io/[\w.-]+/artefacts/(#[\w-]*)?")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
ARTEFACTS_BLOCK_RE = re.compile(
    r"^<!-- ARTEFACTS:START -->(.+?)^<!-- ARTEFACTS:END -->", re.DOTALL | re.MULTILINE
)

errors: list[str] = []


def check_artefacts_block(path: Path, content: str) -> None:
    """Validate the ARTEFACTS block structure."""
    m = ARTEFACTS_BLOCK_RE.search(content)
    if not m:
        return  # No block — not an error (not all files have one)

    block = m.group(1)

    if "## Generated Artefacts" not in block:
        errors.append(
            f"{path}: ARTEFACTS block missing '## Generated Artefacts' heading"
        )

    # Check table structure
    links = MARKDOWN_LINK_RE.findall(block)
    if len(links) < 4:
        errors.append(
            f"{path}: ARTEFACTS block has {len(links)} links, expected at least 4"
        )

    for label, url in links:
        if "github.io" in url:
            check_pages_url(path, url)


def check_pages_url(path: Path, url: str) -> None:
    """Validate a GitHub Pages URL has a valid anchor."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    if fragment and fragment not in VALID_ANCHORS:
        errors.append(f"{path}: invalid anchor '{fragment}' in {url}")

    if not parsed.path.endswith("/artefacts/") and not parsed.path.endswith(
        "/artefacts"
    ):
        # Allow links to the repo itself
        if "github.io" in parsed.netloc and "/artefacts" not in parsed.path:
            return


def check_relative_links(path: Path, content: str) -> None:
    """Validate relative file links resolve."""
    for label, url in MARKDOWN_LINK_RE.findall(content):
        if url.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = (path.parent / url).resolve()
        if not target.exists():
            errors.append(f"{path}: broken relative link [{label}]({url})")


def check_online(url: str) -> bool:
    """HEAD request to verify a URL is live."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "repo-artefacts-link-checker/1.0")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def main() -> int:
    online = "--online" in sys.argv
    md_files = list(REPO_ROOT.glob("*.md")) + list((REPO_ROOT / "docs").glob("*.md"))

    for path in md_files:
        content = path.read_text(encoding="utf-8")
        check_artefacts_block(path, content)
        check_relative_links(path, content)

        if online:
            for _, url in MARKDOWN_LINK_RE.findall(content):
                if "github.io" in url and not check_online(url.split("#")[0]):
                    errors.append(f"{path}: URL not reachable: {url}")

    if errors:
        print(f"\n❌ {len(errors)} link issue(s) found:\n")
        for e in errors:
            print(f"  • {e}")
        return 1

    print(f"✅ All links valid across {len(md_files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
