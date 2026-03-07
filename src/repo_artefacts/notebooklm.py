"""NotebookLM integration for uploading repo content and generating artefacts."""

import asyncio
from pathlib import Path

from notebooklm import AudioFormat, NotebookLMClient, VideoStyle
from rich.console import Console
from rich.table import Table

console = Console()

ARTEFACT_CONFIG: dict[str, dict] = {
    "audio": {
        "instructions": "Create an engaging audio overview of this codebase, explaining its architecture, key components, and how they work together",
        "timeout": 600,
    },
    "video": {
        "instructions": "Create a visual explainer of this codebase architecture and key workflows",
        "timeout": 900,
    },
    "slides": {
        "instructions": "Create a presentation covering the codebase architecture, key components, and workflows",
        "timeout": 300,
    },
    "infographic": {
        "instructions": "Create an infographic showing the codebase architecture, module relationships, and key workflows",
        "timeout": 300,
    },
}

DOWNLOAD_MAP = [
    ("audio", "list_audio", "download_audio", "audio_overview.mp3"),
    ("video", "list_video", "download_video", "video_overview.mp4"),
    ("slides", "list_slide_decks", "download_slide_deck", "slides.pptx"),
    ("infographic", "list_infographics", "download_infographic", "infographic.png"),
]


async def upload_repo(
    content_path: Path,
    repo_name: str,
    notebook_id: str | None = None,
) -> dict:
    """Upload collected repo content to a NotebookLM notebook.

    Checks for existing notebook with matching title before creating a new one.

    Returns:
        Dict with keys: id, title.
    """
    async with await NotebookLMClient.from_storage() as client:
        if notebook_id:
            nb_id = notebook_id
            nb_title = repo_name
            console.print(f"Using existing notebook: [bold]{nb_id}[/bold]")
        else:
            notebooks = await client.notebooks.list()
            existing = next((nb for nb in notebooks if nb.title == repo_name), None)
            if existing:
                nb_id = existing.id
                nb_title = existing.title
                console.print(f"Found existing notebook: [bold]{nb_title}[/bold] ({nb_id})")
            else:
                notebook = await client.notebooks.create(title=repo_name)
                nb_id = notebook.id
                nb_title = notebook.title
                console.print(f"Created notebook: [bold]{nb_title}[/bold] ({nb_id})")

        await client.sources.add_file(nb_id, content_path)
        console.print(f"  [green]✓[/green] Uploaded {content_path.name}")

    return {"id": nb_id, "title": nb_title}


async def generate_artefacts(notebook_id: str, artefacts: list[str]) -> None:
    """Generate requested artefact types concurrently.

    Fires off all generation requests, then polls every 30s until each
    completes or times out.
    """
    async with await NotebookLMClient.from_storage() as client:
        # Fire off all requests
        tasks: dict[str, str] = {}
        for artefact in artefacts:
            cfg = ARTEFACT_CONFIG[artefact]
            console.print(f"[blue]⏳[/blue] Requesting {artefact}...")

            if artefact == "audio":
                status = await client.artifacts.generate_audio(
                    notebook_id,
                    instructions=cfg["instructions"],
                    audio_format=AudioFormat.DEEP_DIVE,
                )
            elif artefact == "video":
                status = await client.artifacts.generate_video(
                    notebook_id,
                    instructions=cfg["instructions"],
                    video_style=VideoStyle.WHITEBOARD,
                )
            elif artefact == "slides":
                status = await client.artifacts.generate_slide_deck(
                    notebook_id,
                    instructions=cfg["instructions"],
                )
            elif artefact == "infographic":
                status = await client.artifacts.generate_infographic(
                    notebook_id,
                    instructions=cfg["instructions"],
                )
            else:
                continue

            tasks[artefact] = status.task_id

        # Poll all concurrently every 30s
        pending = dict(tasks)
        elapsed = 0
        poll_interval = 30
        max_timeout = max(ARTEFACT_CONFIG[a]["timeout"] for a in pending)

        while pending and elapsed < max_timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            for label, task_id in list(pending.items()):
                timeout = ARTEFACT_CONFIG[label]["timeout"]
                if elapsed > timeout:
                    console.print(f"[red]✗[/red] {label.capitalize()} timed out")
                    del pending[label]
                    continue

                result = await client.artifacts.poll_status(notebook_id, task_id)
                if result.is_complete:
                    console.print(f"[green]✓[/green] {label.capitalize()} ready")
                    del pending[label]
                else:
                    console.print(f"[dim]  … {label} still generating ({elapsed}s)[/dim]")

        for label in pending:
            console.print(f"[red]✗[/red] {label.capitalize()} did not complete")

    console.print("[bold green]Done.[/bold green]")


async def download_artefacts(notebook_id: str, output_dir: Path) -> None:
    """Download all available artefacts from a notebook."""
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await NotebookLMClient.from_storage() as client:
        for label, list_method, dl_method, filename in DOWNLOAD_MAP:
            items = await getattr(client.artifacts, list_method)(notebook_id)
            if not items:
                continue
            if len(items) == 1:
                path = str(output_dir / filename)
                await getattr(client.artifacts, dl_method)(
                    notebook_id, path, artifact_id=items[0].id
                )
                console.print(f"[green]✓[/green] Downloaded {path}")
            else:
                stem, ext = filename.rsplit(".", 1)
                for i, artifact in enumerate(items, 1):
                    path = str(output_dir / f"{stem}_{i:02d}.{ext}")
                    await getattr(client.artifacts, dl_method)(
                        notebook_id, path, artifact_id=artifact.id
                    )
                    console.print(f"[green]✓[/green] Downloaded {path}")

    console.print(f"[bold green]Done.[/bold green] Files saved to {output_dir}")


async def list_notebooks() -> None:
    """List all NotebookLM notebooks with source counts."""
    async with await NotebookLMClient.from_storage() as client:
        notebooks = await client.notebooks.list()
        rows = []
        for nb in notebooks:
            sources = await client.sources.list(nb.id)
            rows.append((nb.id, nb.title, str(len(sources))))

    table = Table(title="Notebooks")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Sources", justify="right")
    for row in rows:
        table.add_row(*row)
    console.print(table)


async def list_sources(notebook_id: str) -> None:
    """List all sources in a specific notebook."""
    async with await NotebookLMClient.from_storage() as client:
        sources = await client.sources.list(notebook_id)

    table = Table(title=f"Sources in {notebook_id}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    for i, src in enumerate(sources, 1):
        table.add_row(str(i), src.id, src.title)
    console.print(table)


async def delete_notebook(notebook_id: str) -> None:
    """Delete a notebook and all its contents."""
    async with await NotebookLMClient.from_storage() as client:
        await client.notebooks.delete(notebook_id)
        console.print(f"[green]✓[/green] Deleted notebook {notebook_id}")
