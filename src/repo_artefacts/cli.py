"""CLI entry point for repo-artefacts."""

from __future__ import annotations

import asyncio
import functools
import os
import subprocess
from pathlib import Path

import typer
from rich.table import Table

from repo_artefacts.console import get_console
from repo_artefacts.exceptions import RepoArtefactsError

ALL_ARTEFACTS = ["audio", "video", "slides", "infographic"]

app = typer.Typer(
    help="Generate NotebookLM artefacts (audio, video, slides, infographic) from any git repository.",
)


def _handle_errors(func):  # type: ignore[no-untyped-def]
    """Decorator: catch domain exceptions and translate to typer.Exit."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return func(*args, **kwargs)
        except RepoArtefactsError as exc:
            get_console().print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(code=1) from exc

    return wrapper


def _get_repo_name(repo_path: Path) -> str:
    """Get repo name from git remote origin, falling back to directory name."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            name = url.rstrip("/").rsplit("/", 1)[-1]
            return name.removesuffix(".git")
    except FileNotFoundError:
        pass
    return repo_path.resolve().name


def _get_notebook_id(notebook_id: str | None) -> str:
    """Resolve notebook ID from argument or NOTEBOOK_ID env var."""
    nb_id = notebook_id or os.environ.get("NOTEBOOK_ID")
    if not nb_id:
        get_console().print("[red]No notebook ID. Use -n or set NOTEBOOK_ID env var.[/red]")
        raise typer.Exit(1)
    return nb_id


@app.command()
@_handle_errors
def process(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    output_dir: Path = typer.Option(
        Path("./docs/artefacts"), "--output-dir", "-o", help="Output directory."
    ),
    notebook_id: str | None = typer.Option(
        None,
        "--notebook-id",
        "-n",
        envvar="NOTEBOOK_ID",
        help="Existing NotebookLM notebook ID.",
    ),
) -> None:
    """Collect repo content and upload to NotebookLM."""
    from repo_artefacts.collector import collect_repo_content, render_to_pdf
    from repo_artefacts.notebooklm import upload_repo

    repo_path = repo_path.resolve()
    repo_name = _get_repo_name(repo_path)
    get_console().print(f"[bold]Collecting[/bold] content from [cyan]{repo_name}[/cyan]")

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{repo_name}_content.md"
    collect_repo_content(repo_path, md_path)

    if md_path.stat().st_size == 0 or md_path.read_text().strip() == f"# {repo_name}":
        get_console().print("[red]No content collected. Is this a code repository?[/red]")
        return

    pdf_path = render_to_pdf(md_path)
    result = asyncio.run(upload_repo(pdf_path, repo_name, notebook_id))

    table = Table(title="Notebook")
    table.add_column("Title", style="bold")
    table.add_column("ID", style="cyan")
    table.add_row(result["title"], result["id"])
    get_console().print(table)

    get_console().print("\nTo use this notebook in other commands:")
    get_console().print(f"  export NOTEBOOK_ID={result['id']}")


@app.command()
def generate(
    notebook_id: str | None = typer.Option(
        None,
        "--notebook-id",
        "-n",
        envvar="NOTEBOOK_ID",
        help="Notebook ID to generate from.",
    ),
    audio: bool = typer.Option(False, "--audio", help="Generate audio overview."),
    video: bool = typer.Option(False, "--video", help="Generate video explainer."),
    slides: bool = typer.Option(False, "--slides", help="Generate slide deck."),
    infographic: bool = typer.Option(False, "--infographic", help="Generate infographic."),
    all_: bool = typer.Option(
        False, "--all", help="Generate all artefact types (default if none specified)."
    ),
    timeout: int = typer.Option(
        900,
        "--timeout",
        "-t",
        help="Timeout in seconds per artefact (default: 900 = 15min).",
    ),
) -> None:
    """Generate artefacts from a NotebookLM notebook."""
    from repo_artefacts.notebooklm import generate_artefacts

    nb_id = _get_notebook_id(notebook_id)

    selected = []
    if audio:
        selected.append("audio")
    if video:
        selected.append("video")
    if slides:
        selected.append("slides")
    if infographic:
        selected.append("infographic")

    if all_ or not selected:
        selected = ALL_ARTEFACTS

    get_console().print(f"Generating: [bold]{', '.join(selected)}[/bold]")
    asyncio.run(generate_artefacts(nb_id, selected, timeout=timeout))


@app.command()
def download(
    notebook_id: str | None = typer.Option(
        None,
        "--notebook-id",
        "-n",
        envvar="NOTEBOOK_ID",
        help="Notebook ID to download from.",
    ),
    output_dir: Path = typer.Option(
        Path("./docs/artefacts"), "--output-dir", "-o", help="Output directory."
    ),
) -> None:
    """Download generated artefacts from a notebook."""
    from repo_artefacts.notebooklm import download_artefacts

    nb_id = _get_notebook_id(notebook_id)
    asyncio.run(download_artefacts(nb_id, output_dir))


@app.command("list")
def list_cmd(
    notebook_id: str | None = typer.Option(
        None,
        "--notebook-id",
        "-n",
        envvar="NOTEBOOK_ID",
        help="List sources in this notebook.",
    ),
) -> None:
    """List notebooks, or sources within a notebook."""
    from repo_artefacts.notebooklm import list_notebooks as _list_notebooks
    from repo_artefacts.notebooklm import list_sources

    if notebook_id:
        asyncio.run(list_sources(notebook_id))
    else:
        asyncio.run(_list_notebooks())


@app.command("delete")
def delete_cmd(
    notebook_id: str | None = typer.Option(
        None, "--notebook-id", "-n", envvar="NOTEBOOK_ID", help="Notebook ID to delete."
    ),
) -> None:
    """Delete a notebook and all its contents."""
    from repo_artefacts.notebooklm import delete_notebook

    nb_id = _get_notebook_id(notebook_id)
    typer.confirm(f"Delete notebook {nb_id}?", abort=True)
    asyncio.run(delete_notebook(nb_id))


@app.command()
@_handle_errors
def pages(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    org: str | None = typer.Option(None, "--org", help="GitHub org/user (auto-detected)."),
    repo: str | None = typer.Option(None, "--repo", help="GitHub repo name (auto-detected)."),
) -> None:
    """Set up GitHub Pages player for artefacts.

    Creates an HTML player page at docs/artefacts/index.html, updates README.md
    with Generated Artefacts links, and enables GitHub Pages via API.

    Standard artefact filenames: audio_overview.mp3, video_overview.mp4,
    infographic.png, slides.pdf
    """
    from repo_artefacts.pages import get_github_info, setup_pages

    root = repo_path.resolve()
    if not org or not repo:
        org, repo = get_github_info(root)

    get_console().print(f"[bold]Setting up Pages[/bold] for [cyan]{org}/{repo}[/cyan]")
    url = setup_pages(root, org, repo)
    get_console().print(f"\n[bold green]✅ Done![/bold green] Player: {url}")


@app.command()
@_handle_errors
def publish(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    notebook_id: str | None = typer.Option(
        None, "--notebook-id", "-n", envvar="NOTEBOOK_ID", help="Notebook ID."
    ),
    skip_generate: bool = typer.Option(
        False, "--skip-generate", help="Skip artefact generation (use existing files)."
    ),
    skip_verify: bool = typer.Option(False, "--skip-verify", help="Skip page verification."),
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote to push to."),
    timeout: int = typer.Option(
        900, "--timeout", "-t", help="Generation timeout per artefact (seconds)."
    ),
    verify_timeout: int = typer.Option(
        120, "--verify-timeout", help="Max seconds to wait for Pages deployment."
    ),
    store: str | None = typer.Option(
        None,
        "--store",
        "-s",
        help="Publish to external artefact store (org/repo). Uses config default if set.",
    ),
) -> None:
    """End-to-end: generate artefacts → setup pages → push → verify.

    Generates all NotebookLM artefacts, sets up the GitHub Pages player,
    commits and pushes, then verifies the hosted page is live.

    With --store, publishes artefacts to a separate store repo instead of
    committing binary files into this repo.
    """
    from repo_artefacts.config import load_config
    from repo_artefacts.notebooklm import download_artefacts, generate_artefacts
    from repo_artefacts.pages import get_github_info, get_github_token, setup_pages
    from repo_artefacts.publish import (
        check_artefacts,
        git_commit_and_push,
        verify_pages,
    )

    root = repo_path.resolve()
    org, repo = get_github_info(root)
    store_slug = store or load_config().default_store
    output_dir = root / "docs" / "artefacts"

    get_console().print(f"\n[bold]Publishing artefacts[/bold] for [cyan]{org}/{repo}[/cyan]\n")
    if store_slug:
        get_console().print(f"  Store: [cyan]{store_slug}[/cyan]")

    # Step 1: Generate artefacts
    if not skip_generate:
        nb_id = _get_notebook_id(notebook_id)
        get_console().rule("Step 1: Generate artefacts")
        asyncio.run(generate_artefacts(nb_id, ALL_ARTEFACTS, timeout=timeout))
        asyncio.run(download_artefacts(nb_id, output_dir))

    # Step 2: Check artefacts exist — download from notebook if missing locally
    get_console().rule("Step 2: Check artefacts")
    found = check_artefacts(output_dir)
    if not found:
        nb_id = notebook_id or os.environ.get("NOTEBOOK_ID")
        if nb_id:
            get_console().print("[dim]No local artefacts — downloading from notebook...[/dim]")
            asyncio.run(download_artefacts(nb_id, output_dir))
            found = check_artefacts(output_dir)
    if not found:
        get_console().print("[red]✗ No artefact files found locally or in notebook.[/red]")
        get_console().print("[dim]Use -n NOTEBOOK_ID to download from an existing notebook.[/dim]")
        raise typer.Exit(1)
    for kind, path in found.items():
        get_console().print(f"  [green]✓[/green] {kind}: {path.name}")

    if store_slug:
        # Store mode: publish to artefact store, update source README only
        from repo_artefacts.store import (
            clone_or_pull_store,
            commit_and_push_store,
            publish_to_store,
        )

        get_console().rule("Step 3: Publish to artefact store")
        token = get_github_token()
        store_path = clone_or_pull_store(store_slug, token)
        url = publish_to_store(store_path, repo, output_dir)
        push_ok = commit_and_push_store(store_path, repo)
        if not push_ok:
            raise typer.Exit(1)

        get_console().rule("Step 4: Update source README")
        setup_pages(root, org, repo, store_base_url=url, available_artefacts=set(found))

        get_console().rule("Step 5: Commit and push source")
        git_commit_and_push(root, "docs: update artefact links", remote, outputs=["README.md"])

        if not skip_verify:
            get_console().rule("Step 6: Verify deployment")
            artefact_urls = {kind: url + path.name for kind, path in found.items()}
            verify_pages(url, max_wait=verify_timeout, artefact_urls=artefact_urls)
    else:
        # Local mode: existing behaviour
        get_console().rule("Step 3: Setup GitHub Pages")
        url = setup_pages(root, org, repo)

        get_console().rule("Step 4: Commit and push")
        git_commit_and_push(
            root, "feat: publish NotebookLM artefacts with GitHub Pages player", remote
        )

        if not skip_verify:
            get_console().rule("Step 5: Verify deployment")
            verify_pages(url, max_wait=verify_timeout)

    get_console().print(f"\n[bold green]✅ Published![/bold green] {url}")


@app.command()
@_handle_errors
def pipeline(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    notebook_id: str | None = typer.Option(
        None,
        "--notebook-id",
        "-n",
        envvar="NOTEBOOK_ID",
        help="Existing notebook ID (skips upload).",
    ),
    audio: bool = typer.Option(False, "--audio", help="Generate audio overview."),
    video: bool = typer.Option(False, "--video", help="Generate video explainer."),
    slides: bool = typer.Option(False, "--slides", help="Generate slide deck."),
    infographic: bool = typer.Option(False, "--infographic", help="Generate infographic."),
    exclude: list[str] = typer.Option(
        [],
        "--exclude",
        help="Artefact types to skip (audio, video, slides, infographic). Repeatable.",
    ),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Only generate artefacts not yet completed in the notebook.",
    ),
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote to push to."),
    timeout: int = typer.Option(
        900, "--timeout", "-t", help="Generation timeout per artefact (seconds)."
    ),
    keep_notebook: bool = typer.Option(
        False, "--keep-notebook", help="Don't delete the notebook after publishing."
    ),
    store: str | None = typer.Option(
        None,
        "--store",
        "-s",
        help="Publish to external artefact store (org/repo). Uses config default if set.",
    ),
) -> None:
    """Full pipeline: upload → generate → download → pages → push → verify → cleanup.

    Creates a NotebookLM notebook (or uses existing), generates all artefacts
    with retry, publishes via GitHub Pages, verifies deployment, then deletes
    the notebook since artefacts are now hosted.

    With --store, publishes artefacts to a separate store repo instead of
    committing binary files into this repo. Configure a default store with:

      mkdir -p ~/.config/repo-artefacts && echo 'default_store = "Org/repo"' > ~/.config/repo-artefacts/config.toml

    Artefact selection (pick one mode):

      Default: generate all four types.

      --audio/--video/--slides/--infographic: only generate the specified types.

      --exclude audio --exclude infographic: generate all except the named types.

      --resume: only generate types not yet completed in the notebook.
    """
    from repo_artefacts.collector import collect_repo_content, render_to_pdf
    from repo_artefacts.config import load_config
    from repo_artefacts.notebooklm import (
        GenerateResult,
        delete_notebook,
        download_artefacts,
        generate_artefacts,
        get_completed_artefacts,
        upload_repo,
    )
    from repo_artefacts.pages import get_github_info, setup_pages
    from repo_artefacts.publish import (
        check_artefacts,
        git_commit_and_push,
        verify_pages,
    )

    root = repo_path.resolve()
    org, repo = get_github_info(root)
    output_dir = root / "docs" / "artefacts"
    store_slug = store or load_config().default_store

    get_console().print(f"\n[bold]Full pipeline[/bold] for [cyan]{org}/{repo}[/cyan]")
    if store_slug:
        get_console().print(f"  Store: [cyan]{store_slug}[/cyan]")
    get_console().print()

    # Step 1: Upload to NotebookLM
    if notebook_id:
        nb_id = notebook_id
        get_console().rule("Step 1: Using existing notebook")
        get_console().print(f"  Notebook: {nb_id}")
    else:
        get_console().rule("Step 1: Collect and upload")
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / f"{repo}_content.md"
        collect_repo_content(root, md_path)
        pdf_path = render_to_pdf(md_path)
        result = asyncio.run(upload_repo(pdf_path, repo))
        nb_id = result["id"]
        # Clean up temp files
        md_path.unlink(missing_ok=True)
        pdf_path.unlink(missing_ok=True)

    # Step 2: Resolve which artefacts to generate
    get_console().rule("Step 2: Generate artefacts")

    # Explicit includes take priority
    selected = [
        a
        for a, flag in [
            ("audio", audio),
            ("video", video),
            ("slides", slides),
            ("infographic", infographic),
        ]
        if flag
    ]

    if selected:
        # --audio/--video/etc: only these
        target = selected
    elif exclude:
        # --exclude: all minus excluded
        bad = {e.lower() for e in exclude}
        unknown = bad - set(ALL_ARTEFACTS)
        if unknown:
            get_console().print(
                f"[red]Unknown artefact types: {', '.join(unknown)}."
                f" Valid: {', '.join(ALL_ARTEFACTS)}[/red]"
            )
            raise typer.Exit(1)
        target = [a for a in ALL_ARTEFACTS if a not in bad]
    else:
        # Default: all
        target = list(ALL_ARTEFACTS)

    # --resume or default: skip already-completed
    if resume or (not selected and not exclude):
        already_done = asyncio.run(get_completed_artefacts(nb_id))
        if already_done:
            get_console().print(
                f"  Already completed: [green]{', '.join(sorted(already_done))}[/green]"
            )
        target = [a for a in target if a not in already_done]

    expected = set(target)
    gen_result: GenerateResult | None = None
    if target:
        get_console().print(f"  Generating: [bold]{', '.join(target)}[/bold]")
        gen_result = asyncio.run(generate_artefacts(nb_id, target, timeout=timeout))
    else:
        get_console().print("  [green]All requested artefacts already generated[/green]")

    # Quota-exhausted artefacts can't succeed — don't require them for cleanup
    if gen_result and gen_result.quota_exhausted:
        expected -= gen_result.quota_exhausted

    # Step 3: Download
    get_console().rule("Step 3: Download artefacts")
    asyncio.run(download_artefacts(nb_id, output_dir))

    # Step 4: Check
    get_console().rule("Step 4: Check artefacts")
    found = check_artefacts(output_dir)
    if not found:
        get_console().print("[red]✗ No artefacts downloaded[/red]")
        raise typer.Exit(1)
    for kind, path in found.items():
        get_console().print(f"  [green]✓[/green] {kind}: {path.name}")

    # Steps 5-7: Publish, push, verify — branched by store mode
    artefact_urls: dict[str, str] = {}

    if store_slug:
        from repo_artefacts.pages import get_github_token
        from repo_artefacts.store import (
            clone_or_pull_store,
            commit_and_push_store,
            publish_to_store,
        )

        # Step 5: Publish to artefact store
        get_console().rule("Step 5: Publish to artefact store")
        token = get_github_token()
        store_path = clone_or_pull_store(store_slug, token)
        url = publish_to_store(store_path, repo, output_dir)
        push_ok = commit_and_push_store(store_path, repo)
        if not push_ok:
            get_console().print("[red]Store push failed. Keeping notebook for retry.[/red]")
            get_console().print(
                f"  Resume: [bold]repo-artefacts pipeline . -n {nb_id} --resume"
                f" --store {store_slug}[/bold]"
            )
            raise typer.Exit(1)

        # Step 6: Update source repo README (no binary files)
        get_console().rule("Step 6: Update source README")
        setup_pages(root, org, repo, store_base_url=url, available_artefacts=set(found))
        git_commit_and_push(root, "docs: update artefact links", remote, outputs=["README.md"])

        # Step 7: Verify store deployment
        get_console().rule("Step 7: Verify deployment")
        artefact_urls = {kind: url + path.name for kind, path in found.items()}
        site_ok, verified = verify_pages(url, max_wait=120, artefact_urls=artefact_urls)
    else:
        # Step 5: Local Pages
        get_console().rule("Step 5: Setup GitHub Pages")
        url = setup_pages(root, org, repo)

        # Step 6: Push
        get_console().rule("Step 6: Commit and push")
        git_commit_and_push(
            root, "feat: publish NotebookLM artefacts with GitHub Pages player", remote
        )

        # Step 7: Verify
        get_console().rule("Step 7: Verify deployment")
        artefact_urls = {kind: url + path.name for kind, path in found.items()}
        site_ok, verified = verify_pages(url, max_wait=120, artefact_urls=artefact_urls)

    # Step 8: Cleanup — only delete if all expected artefacts succeeded
    all_expected_downloaded = expected <= set(found)
    all_expected_verified = expected <= verified if site_ok else False
    gen_ok = gen_result is None or not gen_result.failed
    safe_to_delete = gen_ok and all_expected_downloaded and all_expected_verified

    if keep_notebook:
        get_console().print(f"\n[dim]Notebook kept: {nb_id}[/dim]")
    elif safe_to_delete:
        get_console().rule("Step 8: Cleanup notebook")
        location = f"store ({store_slug})" if store_slug else "repo"
        get_console().print(f"  Deleting notebook {nb_id} (artefacts are now in the {location})")
        asyncio.run(delete_notebook(nb_id))
    else:
        get_console().rule("Step 8: Notebook preserved")
        reasons: list[str] = []
        if not gen_ok:
            assert gen_result is not None
            reasons.append(f"generation failed: {', '.join(sorted(gen_result.failed))}")
        if not all_expected_downloaded:
            missing = expected - set(found)
            reasons.append(f"not downloaded: {', '.join(sorted(missing))}")
        if site_ok and not all_expected_verified:
            missing = expected - verified
            reasons.append(f"not verified on Pages: {', '.join(sorted(missing))}")
        get_console().print(
            f"  [yellow]⚠[/yellow] Keeping notebook {nb_id} — {'; '.join(reasons)}"
        )
        get_console().print(
            f"  Resume later: [bold]repo-artefacts pipeline . -n {nb_id} --resume[/bold]"
        )

    get_console().print(f"\n[bold green]✅ Pipeline complete![/bold green] {url}")


@app.command()
@_handle_errors
def migrate(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    store: str | None = typer.Option(
        None,
        "--store",
        "-s",
        help="Artefact store repo (org/repo). Uses config default if set.",
    ),
    remote: str = typer.Option("origin", "--remote", "-r", help="Git remote to push to."),
    skip_verify: bool = typer.Option(False, "--skip-verify", help="Skip deployment verification."),
    verify_timeout: int = typer.Option(
        120, "--verify-timeout", help="Max seconds to wait for Pages deployment."
    ),
    description: str = typer.Option(
        "", "--description", "-d", help="Short description for the store manifest."
    ),
) -> None:
    """Move artefacts from source repo to the artefact store.

    Copies docs/artefacts/ to the store, updates README links, removes
    artefacts from the source repo, and pushes both. Does NOT rewrite
    git history — prints the git-filter-repo command to do that manually.
    """
    from repo_artefacts.config import load_config
    from repo_artefacts.pages import get_github_info, get_github_token, setup_pages
    from repo_artefacts.publish import check_artefacts, verify_pages
    from repo_artefacts.store import clone_or_pull_store, commit_and_push_store, publish_to_store

    root = repo_path.resolve()
    org, repo = get_github_info(root)
    store_slug = store or load_config().default_store
    artefacts_dir = root / "docs" / "artefacts"

    if not store_slug:
        get_console().print(
            "[red]No store configured. Use --store or set default_store in"
            " ~/.config/repo-artefacts/config.toml[/red]"
        )
        raise typer.Exit(1)

    get_console().print(f"\n[bold]Migrating artefacts[/bold] for [cyan]{org}/{repo}[/cyan]")
    get_console().print(f"  Store: [cyan]{store_slug}[/cyan]\n")

    # Step 1: Check artefacts exist locally
    get_console().rule("Step 1: Check local artefacts")
    found = check_artefacts(artefacts_dir)
    if not found:
        get_console().print("[red]No artefact files found in docs/artefacts/[/red]")
        raise typer.Exit(1)
    for kind, path in found.items():
        get_console().print(f"  [green]✓[/green] {kind}: {path.name}")

    # Step 2: Publish to store
    get_console().rule("Step 2: Publish to artefact store")
    token = get_github_token()
    store_path = clone_or_pull_store(store_slug, token)
    url = publish_to_store(store_path, repo, artefacts_dir, description=description)
    push_ok = commit_and_push_store(store_path, repo)
    if not push_ok:
        get_console().print("[red]Store push failed. Source repo unchanged.[/red]")
        raise typer.Exit(1)

    # Step 3: Update source README with store URLs
    get_console().rule("Step 3: Update source README")
    setup_pages(root, org, repo, store_base_url=url, available_artefacts=set(found))

    # Step 4: Remove artefacts from source repo
    get_console().rule("Step 4: Remove artefacts from source repo")
    import subprocess

    # git rm the artefact files (keeps .gitattributes, .gitignore etc.)
    files_to_remove = [str(p.relative_to(root)) for p in artefacts_dir.iterdir() if p.is_file()]
    if files_to_remove:
        result = subprocess.run(
            ["git", "rm", "--quiet", "--", *files_to_remove],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            get_console().print(
                f"  [green]✓[/green] Removed {len(files_to_remove)} files from git"
            )
        else:
            # Some files may not be tracked (untracked artefacts)
            get_console().print(f"  [dim]git rm: {result.stderr.strip()}[/dim]")
            # Force-remove any that are tracked
            subprocess.run(
                ["git", "rm", "--quiet", "--ignore-unmatch", "--", *files_to_remove],
                cwd=root,
                capture_output=True,
            )

    # Step 5: Commit and push (README update + artefact removal)
    get_console().rule("Step 5: Commit and push")
    # Stage README explicitly, artefact removals are already staged by git rm
    subprocess.run(["git", "add", "--", "README.md"], cwd=root, capture_output=True)
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root)
    if result.returncode == 0:
        get_console().print("  No changes to commit")
    else:
        try:
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"chore: migrate artefacts to store ({store_slug})\n\n"
                    "Artefact files moved to centralised artefact-store repo.\n"
                    "README links now point to the store's GitHub Pages site.",
                ],
                cwd=root,
                check=True,
            )
            subprocess.run(["git", "push", remote], cwd=root, check=True)
            get_console().print(f"  [green]✓[/green] Pushed to {remote}")
        except subprocess.CalledProcessError as e:
            get_console().print(f"  [red]✗[/red] Git failed: {e}")
            raise typer.Exit(1) from e

    # Step 6: Verify store deployment
    if not skip_verify:
        get_console().rule("Step 6: Verify deployment")
        artefact_urls = {kind: url + path.name for kind, path in found.items()}
        verify_pages(url, max_wait=verify_timeout, artefact_urls=artefact_urls)

    # Done — suggest history cleanup
    get_console().print(f"\n[bold green]✅ Migration complete![/bold green] {url}")
    get_console().print(
        "\n[dim]Artefacts are now served from the store. The source repo still has"
        " artefact blobs in git history. To shrink the repo:[/dim]\n"
    )
    get_console().print(
        f"  [bold]cd {root}[/bold]\n"
        "  [bold]pip install git-filter-repo[/bold]\n"
        "  [bold]git filter-repo --path docs/artefacts/ --invert-paths[/bold]\n"
        "  [bold]git push --force-with-lease[/bold]\n"
    )
    get_console().print(
        "[dim]This rewrites history and requires a force push."
        " All collaborators will need to re-clone.[/dim]"
    )


@app.command()
@_handle_errors
def validate(
    repo_path: Path = typer.Argument(Path("."), help="Repo to validate README artefact links."),
    all_repos: bool = typer.Option(
        False, "--all", "-a", help="Validate all repos in the artefact store."
    ),
    store: str | None = typer.Option(
        None, "--store", "-s", help="Store repo slug (for --all mode)."
    ),
) -> None:
    """Check that artefact URLs in README are reachable.

    For a single repo: parse README, HEAD each artefact URL.
    With --all: check every repo listed in the store's manifest.
    """
    import json
    import re
    import urllib.error
    import urllib.request

    from rich.table import Table

    if all_repos:
        from repo_artefacts.config import load_config
        from repo_artefacts.pages import get_github_token
        from repo_artefacts.store import clone_or_pull_store

        store_slug = store or load_config().default_store
        if not store_slug:
            get_console().print(
                "[red]No store configured. Use --store or set default_store.[/red]"
            )
            raise typer.Exit(1)

        token = get_github_token()
        store_path = clone_or_pull_store(store_slug, token)
        manifest_path = store_path / "manifest.json"
        if not manifest_path.exists():
            get_console().print("[red]No manifest.json in store.[/red]")
            raise typer.Exit(1)

        manifest = json.loads(manifest_path.read_text())
        repos = manifest.get("repos", [])
        if not repos:
            get_console().print("[yellow]No repos in manifest.[/yellow]")
            return

        # Read CNAME for base URL
        cname_file = store_path / "CNAME"
        if cname_file.exists():
            domain = cname_file.read_text().strip()
            base = f"https://{domain}"
        else:
            base = (
                f"https://{store_slug.split('/')[0].lower()}.github.io/{store_slug.split('/')[1]}"
            )

        table = Table(title="Artefact Link Validation")
        table.add_column("Repo", style="bold")
        table.add_column("Artefact")
        table.add_column("Status")
        table.add_column("URL", style="dim")

        broken = 0
        for repo_entry in repos:
            name = repo_entry["name"]
            for artefact_type in repo_entry.get("artefacts", []):
                from repo_artefacts.publish import STANDARD_FILES

                # Try all possible filenames for this artefact type
                candidates = [fn for fn, kind in STANDARD_FILES.items() if kind == artefact_type]
                if not candidates:
                    continue

                found_ok = False
                last_url = ""
                for filename in candidates:
                    url = f"{base}/{name}/artefacts/{filename}"
                    last_url = url
                    try:
                        req = urllib.request.Request(url, method="HEAD")
                        resp = urllib.request.urlopen(req, timeout=10)
                        if resp.status == 200:
                            table.add_row(name, artefact_type, "[green]OK[/green]", url)
                            found_ok = True
                            break
                    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                        continue

                if not found_ok:
                    table.add_row(name, artefact_type, "[red]NOT FOUND[/red]", last_url)
                    broken += 1

        get_console().print(table)
        if broken:
            get_console().print(f"\n[red]{broken} broken link(s) found.[/red]")
            raise typer.Exit(1)
        get_console().print("\n[green]All links OK.[/green]")
        return

    # Single repo mode: parse README for artefact URLs
    root = repo_path.resolve()
    readme = root / "README.md"
    if not readme.exists():
        get_console().print("[red]No README.md found.[/red]")
        raise typer.Exit(1)

    text = readme.read_text()
    m = re.search(
        r"<!-- ARTEFACTS:START -->(.+?)<!-- ARTEFACTS:END -->",
        text,
        flags=re.DOTALL,
    )
    if not m:
        get_console().print("[yellow]No artefacts block found in README.md[/yellow]")
        return

    urls = re.findall(r"\((https?://[^)]+)\)", m.group(1))
    if not urls:
        get_console().print("[yellow]No URLs found in artefacts block.[/yellow]")
        return

    table = Table(title="Artefact Link Validation")
    table.add_column("URL", style="dim")
    table.add_column("Status")

    broken = 0
    for url in urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=10)
            if resp.status == 200:
                table.add_row(url, "[green]OK[/green]")
            else:
                table.add_row(url, f"[red]HTTP {resp.status}[/red]")
                broken += 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            table.add_row(url, f"[red]{e}[/red]")
            broken += 1

    get_console().print(table)
    if broken:
        get_console().print(f"\n[red]{broken} broken link(s) found.[/red]")
        raise typer.Exit(1)
    get_console().print("\n[green]All links OK.[/green]")


@app.command()
@_handle_errors
def clean(
    store: str | None = typer.Option(
        None, "--store", "-s", help="Artefact store repo (org/repo)."
    ),
    delete: bool = typer.Option(
        False, "--delete", help="Remove orphaned artefact directories and push."
    ),
) -> None:
    """Find orphaned artefacts in the store.

    Lists artefact directories that no longer have a matching source repo
    on GitHub. With --delete, removes them and pushes.
    """
    import urllib.error
    import urllib.request

    from rich.table import Table

    from repo_artefacts.config import load_config
    from repo_artefacts.pages import get_github_token
    from repo_artefacts.store import (
        clone_or_pull_store,
        list_store_repos,
        remove_store_repo,
    )

    store_slug = store or load_config().default_store
    if not store_slug:
        get_console().print("[red]No store configured. Use --store or set default_store.[/red]")
        raise typer.Exit(1)

    org = store_slug.split("/")[0]
    token = get_github_token()
    store_path = clone_or_pull_store(store_slug, token)
    repos = list_store_repos(store_path)

    if not repos:
        get_console().print("[dim]No repo directories in store.[/dim]")
        return

    get_console().print(f"Checking {len(repos)} repos in {store_slug}...\n")

    table = Table(title="Store Repos")
    table.add_column("Repo", style="bold")
    table.add_column("Source Repo")
    table.add_column("Status")

    orphans: list[str] = []
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    for repo_name in repos:
        api_url = f"https://api.github.com/repos/{org}/{repo_name}"
        try:
            req = urllib.request.Request(api_url, headers=headers)
            urllib.request.urlopen(req, timeout=10)
            table.add_row(repo_name, f"{org}/{repo_name}", "[green]exists[/green]")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                table.add_row(repo_name, f"{org}/{repo_name}", "[red]not found[/red]")
                orphans.append(repo_name)
            else:
                table.add_row(repo_name, f"{org}/{repo_name}", f"[yellow]HTTP {e.code}[/yellow]")
        except (urllib.error.URLError, TimeoutError) as e:
            table.add_row(repo_name, f"{org}/{repo_name}", f"[yellow]{e}[/yellow]")

    get_console().print(table)

    if not orphans:
        get_console().print("\n[green]No orphaned artefacts found.[/green]")
        return

    get_console().print(f"\n[yellow]{len(orphans)} orphan(s) found: {', '.join(orphans)}[/yellow]")

    if not delete:
        get_console().print("[dim]Use --delete to remove orphaned directories.[/dim]")
        return

    # Remove orphans and push
    for repo_name in orphans:
        remove_store_repo(store_path, repo_name)
        get_console().print(f"  [green]✓[/green] Removed {repo_name}")

    # Commit removals — stage manifest + removed dirs
    import subprocess

    for repo_name in orphans:
        subprocess.run(
            ["git", "rm", "-r", "--quiet", "--ignore-unmatch", "--", repo_name],
            cwd=store_path,
            capture_output=True,
        )
    subprocess.run(["git", "add", "--", "manifest.json"], cwd=store_path, capture_output=True)

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=store_path)
    if result.returncode == 0:
        get_console().print("  No changes to commit")
        return

    try:
        subprocess.run(
            ["git", "commit", "-m", f"clean: remove orphaned artefacts ({', '.join(orphans)})"],
            cwd=store_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "push"], cwd=store_path, check=True, capture_output=True)
        get_console().print("  [green]✓[/green] Pushed cleanup to store")
    except subprocess.CalledProcessError as e:
        get_console().print(f"  [red]✗[/red] Push failed: {e}")
