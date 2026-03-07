"""NotebookLM integration for uploading repo content and generating artefacts."""

import asyncio
from pathlib import Path

from notebooklm import AudioFormat, GenerationStatus, NotebookLMClient, VideoStyle
from rich.console import Console
from rich.table import Table

console = Console()

ARTEFACT_CONFIG: dict[str, dict] = {
    "audio": {
        "instructions": "Create an engaging audio overview of this codebase, explaining its architecture, key components, and how they work together",
        "timeout": 900,
    },
    "video": {
        "instructions": "Create a visual explainer of this codebase architecture and key workflows",
        "timeout": 900,
    },
    "slides": {
        "instructions": "Create a presentation covering the codebase architecture, key components, and workflows",
        "timeout": 900,
    },
    "infographic": {
        "instructions": "Create an infographic showing the codebase architecture, module relationships, and key workflows",
        "timeout": 900,
    },
}

DOWNLOAD_MAP = [
    ("audio", "list_audio", "download_audio", "audio_overview.mp3"),
    ("video", "list_video", "download_video", "video_overview.mp4"),
    ("slides", "list_slide_decks", "download_slide_deck", "slides.pdf"),
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
                console.print(
                    f"Found existing notebook: [bold]{nb_title}[/bold] ({nb_id})"
                )
            else:
                notebook = await client.notebooks.create(title=repo_name)
                nb_id = notebook.id
                nb_title = notebook.title
                console.print(f"Created notebook: [bold]{nb_title}[/bold] ({nb_id})")

        await client.sources.add_file(nb_id, content_path)
        console.print(f"  [green]✓[/green] Uploaded {content_path.name}")

    return {"id": nb_id, "title": nb_title}


MAX_RETRIES = 3


async def _request_artefact(
    client: NotebookLMClient, notebook_id: str, artefact: str
) -> GenerationStatus:
    """Fire off a single generation request. Returns GenerationStatus."""
    cfg = ARTEFACT_CONFIG[artefact]
    if artefact == "audio":
        return await client.artifacts.generate_audio(
            notebook_id,
            instructions=cfg["instructions"],
            audio_format=AudioFormat.DEEP_DIVE,
        )
    elif artefact == "video":
        return await client.artifacts.generate_video(
            notebook_id,
            instructions=cfg["instructions"],
            video_style=VideoStyle.WHITEBOARD,
        )
    elif artefact == "slides":
        return await client.artifacts.generate_slide_deck(
            notebook_id,
            instructions=cfg["instructions"],
        )
    elif artefact == "infographic":
        return await client.artifacts.generate_infographic(
            notebook_id,
            instructions=cfg["instructions"],
        )
    else:
        raise ValueError(f"Unknown artefact type: {artefact}")


async def generate_artefacts(
    notebook_id: str, artefacts: list[str], timeout: int = 900
) -> None:
    """Generate requested artefact types concurrently with retry on failure.

    Fires off all generation requests, polls every 30s. If an artefact
    fails (either at request time or during polling), retries up to
    MAX_RETRIES times.
    """
    async with await NotebookLMClient.from_storage() as client:
        pending: dict[str, str] = {}  # artefact -> task_id
        retries: dict[str, int] = {a: 0 for a in artefacts}

        for artefact in artefacts:
            console.print(f"[blue]⏳[/blue] Requesting {artefact}...")
            try:
                status = await _request_artefact(client, notebook_id, artefact)
                if status.is_failed or not status.task_id:
                    retries[artefact] += 1
                    console.print(
                        f"[yellow]⚠[/yellow] {artefact} failed immediately"
                        f" ({status.error or 'no task_id'})"
                        f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                    )
                else:
                    pending[artefact] = status.task_id
            except Exception as e:
                retries[artefact] += 1
                console.print(
                    f"[yellow]⚠[/yellow] Failed to request {artefact}: {e}"
                    f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                )

        # Queue initial failures for retry
        needs_retry = [
            a for a in artefacts if a not in pending and retries[a] <= MAX_RETRIES
        ]

        elapsed = 0
        poll_interval = 30

        console.print(
            f"[dim]Timeout: {timeout}s ({timeout // 60}min),"
            f" max retries: {MAX_RETRIES}[/dim]"
        )

        while (pending or needs_retry) and elapsed < timeout:
            # Retry any queued failures
            for label in list(needs_retry):
                console.print(
                    f"[blue]⏳[/blue] Retrying {label}"
                    f" ({retries[label]}/{MAX_RETRIES})..."
                )
                try:
                    status = await _request_artefact(client, notebook_id, label)
                    if status.is_failed or not status.task_id:
                        retries[label] += 1
                        if retries[label] > MAX_RETRIES:
                            console.print(
                                f"[red]✗[/red] {label} failed after"
                                f" {MAX_RETRIES} retries: {status.error}"
                            )
                            needs_retry.remove(label)
                        # else stays in needs_retry for next loop
                    else:
                        pending[label] = status.task_id
                        needs_retry.remove(label)
                except Exception as e:
                    retries[label] += 1
                    if retries[label] > MAX_RETRIES:
                        console.print(
                            f"[red]✗[/red] {label} failed after"
                            f" {MAX_RETRIES} retries: {e}"
                        )
                        needs_retry.remove(label)

            if not pending and not needs_retry:
                break

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            for label, task_id in list(pending.items()):
                try:
                    result = await client.artifacts.poll_status(notebook_id, task_id)
                except Exception as e:
                    console.print(f"[yellow]⚠[/yellow] Poll error for {label}: {e}")
                    continue

                if result.is_complete:
                    console.print(f"[green]✓[/green] {label.capitalize()} ready")
                    del pending[label]
                elif result.is_failed:
                    del pending[label]
                    retries[label] += 1
                    if retries[label] <= MAX_RETRIES:
                        console.print(
                            f"[yellow]⚠[/yellow] {label} failed"
                            f" ({result.error or 'unknown'})"
                            f" — queued retry ({retries[label]}/{MAX_RETRIES})"
                        )
                        needs_retry.append(label)
                    else:
                        console.print(
                            f"[red]✗[/red] {label} failed after"
                            f" {MAX_RETRIES} retries: {result.error}"
                        )
                else:
                    console.print(
                        f"[dim]  … {label} still generating ({elapsed}s)[/dim]"
                    )

        for label in list(pending) + needs_retry:
            console.print(f"[red]✗[/red] {label.capitalize()} timed out")

    console.print("[bold green]Done.[/bold green]")


async def download_artefacts(notebook_id: str, output_dir: Path) -> None:
    """Download all available artefacts from a notebook."""
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await NotebookLMClient.from_storage() as client:
        for label, list_method, dl_method, filename in DOWNLOAD_MAP:
            items = await getattr(client.artifacts, list_method)(notebook_id)
            if not items:
                continue
            # Skip failed artefacts
            ready = [i for i in items if i.is_completed]
            if not ready:
                console.print(
                    f"[yellow]⚠[/yellow] {label}: exists but not ready (failed or processing), skipping"
                )
                continue
            if len(ready) == 1:
                path = str(output_dir / filename)
                await getattr(client.artifacts, dl_method)(
                    notebook_id, path, artifact_id=ready[0].id
                )
                console.print(f"[green]✓[/green] Downloaded {path}")
            else:
                stem, ext = filename.rsplit(".", 1)
                for i, artifact in enumerate(ready, 1):
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
