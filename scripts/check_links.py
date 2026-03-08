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


def check_artefacts_block(path: Path, content: str) -> list[str]:
    """Validate the ARTEFACTS block structure. Returns list of errors."""
    errors: list[str] = []
    m = ARTEFACTS_BLOCK_RE.search(content)
    if not m:
        return errors  # No block — not an error (not all files have one)

    block = m.group(1)

    if "## Generated Artefacts" not in block:
        errors.append(f"{path}: ARTEFACTS block missing '## Generated Artefacts' heading")

    # Check table structure
    links = MARKDOWN_LINK_RE.findall(block)
    if len(links) < 4:
        errors.append(f"{path}: ARTEFACTS block has {len(links)} links, expected at least 4")

    for _label, url in links:
        if "github.io" in url:
            errors.extend(check_pages_url(path, url))

    return errors


def check_pages_url(path: Path, url: str) -> list[str]:
    """Validate a GitHub Pages URL has a valid anchor. Returns list of errors."""
    from urllib.parse import urlparse

    errors: list[str] = []
    parsed = urlparse(url)
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    if fragment and fragment not in VALID_ANCHORS:
        errors.append(f"{path}: invalid anchor '{fragment}' in {url}")
    return errors


def check_relative_links(path: Path, content: str) -> list[str]:
    """Validate relative file links resolve. Returns list of errors."""
    errors: list[str] = []
    for label, url in MARKDOWN_LINK_RE.findall(content):
        if url.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = (path.parent / url).resolve()
        if not target.exists():
            errors.append(f"{path}: broken relative link [{label}]({url})")
    return errors


def check_online(url: str) -> bool:
    """HEAD request to verify a URL is live."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", "repo-artefacts-link-checker/1.0")
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return False


def check_all(repo_root: Path | None = None, *, online: bool = False) -> list[str]:
    """Run all checks. Returns aggregated error list."""
    root = repo_root or REPO_ROOT
    errors: list[str] = []
    md_files = list(root.glob("*.md")) + list((root / "docs").glob("*.md"))

    for path in md_files:
        content = path.read_text(encoding="utf-8")
        errors.extend(check_artefacts_block(path, content))
        errors.extend(check_relative_links(path, content))

        if online:
            for _, url in MARKDOWN_LINK_RE.findall(content):
                if "github.io" in url and not check_online(url.split("#")[0]):
                    errors.append(f"{path}: URL not reachable: {url}")

    return errors


def main() -> int:
    online = "--online" in sys.argv
    errors = check_all(online=online)

    if errors:
        print(f"\n{len(errors)} link issue(s) found:\n")
        for e in errors:
            print(f"  - {e}")
        return 1

    md_count = len(list(REPO_ROOT.glob("*.md"))) + len(list((REPO_ROOT / "docs").glob("*.md")))
    print(f"All links valid across {md_count} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
