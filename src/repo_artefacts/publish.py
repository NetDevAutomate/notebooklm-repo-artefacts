"""End-to-end workflow: generate artefacts → setup pages → verify."""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from repo_artefacts.console import get_console

STANDARD_FILES = {
    "audio_overview.m4a": "audio",
    "audio_overview.mp3": "audio",
    "video_overview.mp4": "video",
    "video_overview.webm": "video",
    "infographic.png": "infographic",
    "infographic.jpg": "infographic",
    "infographic.webp": "infographic",
    "slides.pdf": "slides",
}


def check_artefacts(artefacts_dir: Path) -> dict[str, Path]:
    """Check which standard artefact files exist."""
    found: dict[str, Path] = {}
    for name, kind in STANDARD_FILES.items():
        path = artefacts_dir / name
        if path.exists() and kind not in found:
            found[kind] = path
    return found


def verify_pages(
    url: str,
    max_wait: int = 120,
    artefact_urls: dict[str, str] | None = None,
) -> tuple[bool, set[str]]:
    """Poll the GitHub Pages URL until it returns 200 or timeout.

    Returns (site_ok, verified_artefact_types).
    """
    get_console().print(f"\n[bold]Verifying[/bold] {url}")
    verified: set[str] = set()
    start = time.time()
    while time.time() - start < max_wait:
        try:
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req)
            if resp.status == 200:
                get_console().print("[green]✓[/green] Pages site is live!")
                if artefact_urls:
                    for kind, artefact_url in artefact_urls.items():
                        try:
                            areq = urllib.request.Request(artefact_url, method="HEAD")
                            aresp = urllib.request.urlopen(areq)
                            if aresp.status == 200:
                                get_console().print(f"  [green]✓[/green] {kind}: {artefact_url}")
                                verified.add(kind)
                            else:
                                get_console().print(f"  [red]✗[/red] {kind}: HTTP {aresp.status}")
                        except (urllib.error.HTTPError, urllib.error.URLError) as e:
                            get_console().print(f"  [red]✗[/red] {kind}: {e}")
                return True, verified
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
        get_console().print("  Waiting for deployment...", style="dim")
        time.sleep(10)

    get_console().print("[red]✗[/red] Pages site not responding after timeout")
    return False, verified


# Paths this tool is allowed to stage
TOOL_OUTPUTS: list[str] = ["docs/artefacts/", "README.md"]


def _get_current_branch(repo_root: Path) -> str | None:
    """Detect the current branch. Returns None if detached HEAD."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return None if branch == "HEAD" else branch


def _stage_tool_outputs(repo_root: Path, outputs: list[str] | None = None) -> list[str]:
    """Stage only files this tool creates. Returns list of staged paths."""
    staged: list[str] = []
    for rel_path in outputs or TOOL_OUTPUTS:
        full_path = repo_root / rel_path
        if not full_path.exists():
            continue
        result = subprocess.run(
            ["git", "add", "--", rel_path],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            staged.append(rel_path)
        else:
            get_console().print(
                f"[yellow]Warning:[/yellow] could not stage {rel_path}: {result.stderr.strip()}"
            )
    return staged


def git_commit_and_push(
    repo_root: Path,
    message: str,
    remote: str = "origin",
    branch: str | None = None,
    outputs: list[str] | None = None,
) -> bool:
    """Stage tool outputs, commit, and push.

    Safety guarantees:
    - Only stages paths in TOOL_OUTPUTS (never git add -A)
    - Respects pre-commit hooks (no --no-verify)
    - Auto-detects branch (refuses on detached HEAD)
    - Skips commit if nothing to commit

    Args:
        outputs: Override TOOL_OUTPUTS with custom paths to stage.
    """
    if branch is None:
        branch = _get_current_branch(repo_root)
        if branch is None:
            get_console().print(
                "[red]Cannot push: HEAD is detached. Check out a branch first.[/red]"
            )
            return False

    try:
        staged = _stage_tool_outputs(repo_root, outputs)
        if not staged:
            get_console().print("  No tool outputs to stage")
            return True

        # Check if staging produced changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root,
        )
        if result.returncode == 0:
            get_console().print("  No changes to commit")
            return True

        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo_root,
            check=True,
        )
        subprocess.run(
            ["git", "push", remote, branch],
            cwd=repo_root,
            check=True,
        )
        get_console().print(f"[green]✓[/green] Pushed to {remote}/{branch}")
        return True
    except subprocess.CalledProcessError as e:
        get_console().print(f"[red]✗[/red] Git failed: {e}")
        return False
