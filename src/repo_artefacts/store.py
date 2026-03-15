"""Artefact store operations: clone, publish, manifest updates."""

from __future__ import annotations

import html as html_mod
import importlib.resources
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from repo_artefacts.config import load_config
from repo_artefacts.console import get_console
from repo_artefacts.exceptions import RepoArtefactsError
from repo_artefacts.publish import check_artefacts


class StoreError(RepoArtefactsError):
    """Error during artefact store operations."""


def _validate_store_slug(store_slug: str) -> None:
    """Reject store slugs that could resolve to dangerous paths.

    A valid slug looks like ``Org/repo`` — never an absolute path, never
    containing ``..``, and never empty.  Getting this wrong is catastrophic:
    ``Path(base) / "/absolute"`` silently discards the base in Python, so an
    absolute path passed as a slug would make the cache dir point at the
    real filesystem location — and ``shutil.rmtree`` would delete it.
    """
    if not store_slug or not store_slug.strip():
        raise StoreError("Store slug must not be empty")
    if store_slug.startswith(("/", "~")):
        raise StoreError(f"Store slug must be an org/repo identifier, not a path: {store_slug}")
    if ".." in store_slug.split("/"):
        raise StoreError(f"Store slug must not contain '..': {store_slug}")
    parts = store_slug.strip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise StoreError(f"Store slug must be in 'org/repo' format, got: {store_slug}")


def _store_cache_dir(store_slug: str) -> Path:
    """Return cache directory for a store, e.g. ~/.cache/repo-artefacts/stores/Org/repo."""
    _validate_store_slug(store_slug)
    cfg = load_config()
    return cfg.store_cache_dir / store_slug


def _safe_rmtree(path: Path) -> None:
    """Remove a directory only if it lives inside the expected cache tree.

    Defense-in-depth: even if ``_validate_store_slug`` is bypassed or the
    cache dir config changes, never delete a directory outside the cache.
    """
    cfg = load_config()
    try:
        path.resolve().relative_to(cfg.store_cache_dir.resolve())
    except ValueError:
        raise StoreError(
            f"Refusing to delete {path} — it is outside the store cache "
            f"directory ({cfg.store_cache_dir})"
        ) from None
    shutil.rmtree(path)


def clone_or_pull_store(store_slug: str, token: str | None = None) -> Path:
    """Shallow clone artefact-store repo, or pull if cached.

    Args:
        store_slug: GitHub org/repo (e.g., "NetDevAutomate/artefact-store").
        token: GitHub token for HTTPS auth.

    Returns:
        Path to local store checkout.
    """
    cache_dir = _store_cache_dir(store_slug)

    if (cache_dir / ".git").is_dir():
        get_console().print(f"  Pulling latest from {store_slug}...")
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=cache_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            get_console().print(
                f"[yellow]Pull failed, re-cloning: {result.stderr.strip()}[/yellow]"
            )
            _safe_rmtree(cache_dir)
        else:
            return cache_dir

    # Clone fresh (shallow)
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{store_slug}.git"
    else:
        clone_url = f"https://github.com/{store_slug}.git"

    get_console().print(f"  Cloning {store_slug} (shallow)...")
    result = subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, str(cache_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise StoreError(f"Failed to clone {store_slug}: {result.stderr.strip()}")

    return cache_dir


def publish_to_store(
    store_path: Path,
    repo_name: str,
    artefacts_dir: Path,
    description: str = "",
) -> str:
    """Copy artefacts to store and generate player page.

    Args:
        store_path: Path to local store checkout.
        repo_name: Name of the source repo (directory name in store).
        artefacts_dir: Path to local directory containing artefact files.
        description: Short description for the manifest entry.

    Returns:
        Base URL for this repo's artefacts on the store's Pages site.
    """
    dest = store_path / repo_name / "artefacts"
    dest.mkdir(parents=True, exist_ok=True)

    # Copy artefact files
    found = check_artefacts(artefacts_dir)
    if not found:
        raise StoreError(f"No artefact files found in {artefacts_dir}")

    for kind, src_path in found.items():
        dst_path = dest / src_path.name
        shutil.copy2(src_path, dst_path)
        get_console().print(f"  [green]✓[/green] {kind}: {src_path.name}")

    # Write player page from template
    template = importlib.resources.files("repo_artefacts").joinpath("template.html").read_text()
    player_html = template.replace("{REPO_NAME}", html_mod.escape(repo_name, quote=True))
    (dest / "index.html").write_text(player_html)
    get_console().print(f"  [green]✓[/green] Player page: {repo_name}/artefacts/index.html")

    # Update manifest
    update_manifest(
        store_path,
        repo_name,
        title=repo_name.replace("-", " ").replace("_", " ").title(),
        description=description,
        artefacts=list(found.keys()),
    )

    # Resolve base URL from CNAME or fallback
    cname_file = store_path / "CNAME"
    if cname_file.exists():
        domain = cname_file.read_text().strip()
        return f"https://{domain}/{repo_name}/artefacts/"

    # Fallback: infer from git remote
    store_slug = store_path.parent.name + "/" + store_path.name
    return f"https://{store_slug.split('/')[0].lower()}.github.io/{store_slug.split('/')[1]}/{repo_name}/artefacts/"


def update_manifest(
    store_path: Path,
    repo_name: str,
    title: str,
    description: str,
    artefacts: list[str],
) -> None:
    """Add or update a repo entry in manifest.json."""
    manifest_path = store_path / "manifest.json"

    data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"repos": []}

    repos: list[dict] = data.get("repos", [])

    entry = {
        "name": repo_name,
        "title": title,
        "description": description,
        "artefacts": artefacts,
        "updated": datetime.now(UTC).strftime("%Y-%m-%d"),
    }

    # Upsert by name
    for i, r in enumerate(repos):
        if r.get("name") == repo_name:
            repos[i] = entry
            break
    else:
        repos.append(entry)

    data["repos"] = repos
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")
    get_console().print("  [green]✓[/green] Updated manifest.json")


def commit_and_push_store(store_path: Path, repo_name: str) -> bool:
    """Stage, commit, and push store changes.

    Stages only: <repo_name>/artefacts/, manifest.json
    On push conflict: pull --rebase, then retry once.
    """
    try:
        # Stage relevant paths
        for rel_path in [f"{repo_name}/artefacts/", "manifest.json"]:
            subprocess.run(
                ["git", "add", "--", rel_path],
                cwd=store_path,
                check=True,
                capture_output=True,
            )

        # Check if there are changes
        result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=store_path)
        if result.returncode == 0:
            get_console().print("  No changes to commit in store")
            return True

        subprocess.run(
            ["git", "commit", "-m", f"artefacts: update {repo_name}"],
            cwd=store_path,
            check=True,
            capture_output=True,
        )

        # Push with one retry on conflict
        push = subprocess.run(
            ["git", "push"],
            cwd=store_path,
            capture_output=True,
            text=True,
        )
        if push.returncode != 0:
            get_console().print("  [dim]Push conflict — pulling and retrying...[/dim]")
            subprocess.run(
                ["git", "pull", "--rebase"],
                cwd=store_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "push"],
                cwd=store_path,
                check=True,
                capture_output=True,
            )

        get_console().print("  [green]✓[/green] Pushed artefacts to store")
        return True
    except subprocess.CalledProcessError as e:
        get_console().print(f"  [red]✗[/red] Store push failed: {e}")
        return False


def list_store_repos(store_path: Path) -> list[str]:
    """List repo directories in the store (for clean command)."""
    return sorted(
        d.name
        for d in store_path.iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "artefacts").is_dir()
    )


def remove_store_repo(store_path: Path, repo_name: str) -> None:
    """Remove a repo's artefacts directory from the store."""
    target = store_path / repo_name
    if target.exists():
        shutil.rmtree(target)

    # Remove from manifest
    manifest_path = store_path / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text())
        data["repos"] = [r for r in data.get("repos", []) if r.get("name") != repo_name]
        manifest_path.write_text(json.dumps(data, indent=2) + "\n")
