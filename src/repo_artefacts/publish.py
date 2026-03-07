"""End-to-end workflow: generate artefacts → setup pages → verify."""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console

console = Console()

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


def verify_pages(url: str, max_wait: int = 120) -> bool:
    """Poll the GitHub Pages URL until it returns 200 or timeout."""
    console.print(f"\n[bold]Verifying[/bold] {url}")
    start = time.time()
    while time.time() - start < max_wait:
        try:
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req)
            if resp.status == 200:
                console.print("[green]✓[/green] Pages site is live!")
                return True
        except (urllib.error.HTTPError, urllib.error.URLError):
            pass
        console.print("  Waiting for deployment...", style="dim")
        time.sleep(10)

    console.print("[red]✗[/red] Pages site not responding after timeout")
    return False


def git_commit_and_push(
    repo_root: Path,
    message: str,
    remote: str = "origin",
    branch: str = "main",
) -> bool:
    """Stage all changes, commit, and push."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
        # Check if there's anything to commit
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root,
        )
        if result.returncode == 0:
            console.print("  No changes to commit")
            return True
        subprocess.run(
            ["git", "commit", "--no-verify", "-m", message],
            cwd=repo_root,
            check=True,
        )
        subprocess.run(
            ["git", "push", remote, branch],
            cwd=repo_root,
            check=True,
        )
        console.print(f"[green]✓[/green] Pushed to {remote}/{branch}")
        return True
    except subprocess.CalledProcessError as e:
        console.print(f"[red]✗[/red] Git failed: {e}")
        return False
