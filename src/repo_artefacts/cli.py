"""CLI entry point for repo-artefacts."""

import asyncio
import subprocess
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()

ALL_ARTEFACTS = ["audio", "video", "slides", "infographic"]


def _get_repo_name(repo_path: Path) -> str:
    """Get repo name from git remote origin, falling back to directory name."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=repo_path,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Handle both https and ssh URLs
            name = url.rstrip("/").rsplit("/", 1)[-1]
            return name.removesuffix(".git")
    except FileNotFoundError:
        pass
    return repo_path.resolve().name


@click.group()
def main() -> None:
    """Generate NotebookLM artefacts (audio, video, slides, infographic) from any git repository."""


@main.command()
@click.argument("repo_path", type=click.Path(exists=True, path_type=Path), default=".")
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=Path("./docs/artefacts"))
@click.option("-n", "--notebook-id", default=None, help="Existing NotebookLM notebook ID.")
def process(repo_path: Path, output_dir: Path, notebook_id: str | None) -> None:
    """Collect repo content and upload to NotebookLM.

    REPO_PATH defaults to the current directory.
    """
    from repo_artefacts.collector import collect_repo_content
    from repo_artefacts.notebooklm import upload_repo

    repo_path = repo_path.resolve()
    repo_name = _get_repo_name(repo_path)
    console.print(f"[bold]Collecting[/bold] content from [cyan]{repo_name}[/cyan]")

    output_dir.mkdir(parents=True, exist_ok=True)
    content_path = output_dir / f"{repo_name}_content.md"
    collect_repo_content(repo_path, content_path)

    result = asyncio.run(upload_repo(content_path, repo_name, notebook_id))

    table = Table(title="Notebook")
    table.add_column("Title", style="bold")
    table.add_column("ID", style="cyan")
    table.add_row(result["title"], result["id"])
    console.print(table)


@main.command()
@click.option("-n", "--notebook-id", required=True, help="Notebook ID to generate from.")
@click.option("--audio", "audio", is_flag=True, help="Generate audio overview.")
@click.option("--video", "video", is_flag=True, help="Generate video explainer.")
@click.option("--slides", "slides", is_flag=True, help="Generate slide deck.")
@click.option("--infographic", "infographic", is_flag=True, help="Generate infographic.")
@click.option("--all", "all_", is_flag=True, help="Generate all artefact types (default if none specified).")
def generate(notebook_id: str, audio: bool, video: bool, slides: bool, infographic: bool, all_: bool) -> None:
    """Generate artefacts from a NotebookLM notebook."""
    from repo_artefacts.notebooklm import generate_artefacts

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
    asyncio.run(generate_artefacts(notebook_id, selected))


@main.command()
@click.option("-n", "--notebook-id", required=True, help="Notebook ID to download from.")
@click.option("-o", "--output-dir", type=click.Path(path_type=Path), default=Path("./docs/artefacts"))
def download(notebook_id: str, output_dir: Path) -> None:
    """Download generated artefacts from a notebook."""
    from repo_artefacts.notebooklm import download_artefacts

    asyncio.run(download_artefacts(notebook_id, output_dir))


@main.command("list")
@click.option("-n", "--notebook-id", default=None, help="List sources in this notebook.")
def list_notebooks(notebook_id: str | None) -> None:
    """List notebooks, or sources within a notebook."""
    from repo_artefacts.notebooklm import list_notebooks as _list_notebooks
    from repo_artefacts.notebooklm import list_sources

    if notebook_id:
        asyncio.run(list_sources(notebook_id))
    else:
        asyncio.run(_list_notebooks())


@main.command("delete")
@click.option("-n", "--notebook-id", required=True, help="Notebook ID to delete.")
@click.confirmation_option(prompt="Are you sure you want to delete this notebook?")
def delete_cmd(notebook_id: str) -> None:
    """Delete a notebook and all its contents."""
    from repo_artefacts.notebooklm import delete_notebook

    asyncio.run(delete_notebook(notebook_id))
