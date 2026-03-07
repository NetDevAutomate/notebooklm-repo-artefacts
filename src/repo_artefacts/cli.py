"""CLI entry point for repo-artefacts."""

import asyncio
import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()

ALL_ARTEFACTS = ["audio", "video", "slides", "infographic"]

app = typer.Typer(
    help="Generate NotebookLM artefacts (audio, video, slides, infographic) from any git repository.",
)


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
        console.print("[red]No notebook ID. Use -n or set NOTEBOOK_ID env var.[/red]")
        raise typer.Exit(1)
    return nb_id


@app.command()
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
    console.print(f"[bold]Collecting[/bold] content from [cyan]{repo_name}[/cyan]")

    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{repo_name}_content.md"
    collect_repo_content(repo_path, md_path)

    if md_path.stat().st_size == 0 or md_path.read_text().strip() == f"# {repo_name}":
        console.print("[red]No content collected. Is this a code repository?[/red]")
        return

    pdf_path = render_to_pdf(md_path)
    result = asyncio.run(upload_repo(pdf_path, repo_name, notebook_id))

    table = Table(title="Notebook")
    table.add_column("Title", style="bold")
    table.add_column("ID", style="cyan")
    table.add_row(result["title"], result["id"])
    console.print(table)

    console.print("\nTo use this notebook in other commands:")
    console.print(f"  export NOTEBOOK_ID={result['id']}")


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
    infographic: bool = typer.Option(
        False, "--infographic", help="Generate infographic."
    ),
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

    console.print(f"Generating: [bold]{', '.join(selected)}[/bold]")
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
    from repo_artefacts.readme_updater import update_readme_artefacts

    nb_id = _get_notebook_id(notebook_id)
    asyncio.run(download_artefacts(nb_id, output_dir))

    # Auto-update README if it exists
    readme = Path("README.md")
    if readme.is_file():
        update_readme_artefacts(readme, output_dir)


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


@app.command("update-readme")
def update_readme(
    readme: Path = typer.Option(
        Path("README.md"), "--readme", "-r", help="Path to README.md."
    ),
    artefacts_dir: Path = typer.Option(
        Path("./docs/artefacts"),
        "--artefacts-dir",
        "-a",
        help="Path to artefacts directory.",
    ),
) -> None:
    """Update README.md with a listing of generated artefacts.

    Inserts or updates content between <!-- ARTEFACTS:START --> and
    <!-- ARTEFACTS:END --> markers. If markers don't exist, appends to the end.
    """
    from repo_artefacts.readme_updater import update_readme_artefacts

    update_readme_artefacts(readme, artefacts_dir)


@app.command()
def pages(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    org: str | None = typer.Option(
        None, "--org", help="GitHub org/user (auto-detected)."
    ),
    repo: str | None = typer.Option(
        None, "--repo", help="GitHub repo name (auto-detected)."
    ),
) -> None:
    """Set up GitHub Pages player for artefacts.

    Creates an HTML player page at docs/artefacts/index.html, updates README.md
    with Repo Deep Dive links, and enables GitHub Pages via API.

    Standard artefact filenames: audio_overview.m4a, video_overview.mp4,
    infographic.png, slides.pdf
    """
    from repo_artefacts.pages import get_github_info, setup_pages

    root = repo_path.resolve()
    if not org or not repo:
        org, repo = get_github_info(root)

    console.print(f"[bold]Setting up Pages[/bold] for [cyan]{org}/{repo}[/cyan]")
    url = setup_pages(root, org, repo)
    console.print(f"\n[bold green]✅ Done![/bold green] Player: {url}")


@app.command()
def publish(
    repo_path: Path = typer.Argument(Path("."), help="Path to git repository."),
    notebook_id: str | None = typer.Option(
        None, "--notebook-id", "-n", envvar="NOTEBOOK_ID", help="Notebook ID."
    ),
    skip_generate: bool = typer.Option(
        False, "--skip-generate", help="Skip artefact generation (use existing files)."
    ),
    skip_verify: bool = typer.Option(
        False, "--skip-verify", help="Skip page verification."
    ),
    remote: str = typer.Option(
        "origin", "--remote", "-r", help="Git remote to push to."
    ),
    timeout: int = typer.Option(
        900, "--timeout", "-t", help="Generation timeout per artefact (seconds)."
    ),
    verify_timeout: int = typer.Option(
        120, "--verify-timeout", help="Max seconds to wait for Pages deployment."
    ),
) -> None:
    """End-to-end: generate artefacts → setup pages → push → verify.

    Generates all NotebookLM artefacts, sets up the GitHub Pages player,
    commits and pushes, then verifies the hosted page is live.
    """
    from repo_artefacts.notebooklm import download_artefacts, generate_artefacts
    from repo_artefacts.pages import get_github_info, setup_pages
    from repo_artefacts.publish import (
        check_artefacts,
        git_commit_and_push,
        verify_pages,
    )

    root = repo_path.resolve()
    org, repo = get_github_info(root)
    output_dir = root / "docs" / "artefacts"

    console.print(
        f"\n[bold]Publishing artefacts[/bold] for [cyan]{org}/{repo}[/cyan]\n"
    )

    # Step 1: Generate artefacts
    if not skip_generate:
        nb_id = _get_notebook_id(notebook_id)
        console.rule("Step 1: Generate artefacts")
        asyncio.run(generate_artefacts(nb_id, ALL_ARTEFACTS, timeout=timeout))
        asyncio.run(download_artefacts(nb_id, output_dir))

    # Step 2: Check artefacts exist
    console.rule("Step 2: Check artefacts")
    found = check_artefacts(output_dir)
    if not found:
        console.print(
            "[red]✗ No standard artefact files found in docs/artefacts/[/red]"
        )
        raise typer.Exit(1)
    for kind, path in found.items():
        console.print(f"  [green]✓[/green] {kind}: {path.name}")

    # Step 3: Setup pages
    console.rule("Step 3: Setup GitHub Pages")
    url = setup_pages(root, org, repo)

    # Step 4: Commit and push
    console.rule("Step 4: Commit and push")
    git_commit_and_push(
        root, "feat: publish NotebookLM artefacts with GitHub Pages player", remote
    )

    # Step 5: Verify
    if not skip_verify:
        console.rule("Step 5: Verify deployment")
        verify_pages(url, max_wait=verify_timeout)

    console.print(f"\n[bold green]✅ Published![/bold green] {url}")
