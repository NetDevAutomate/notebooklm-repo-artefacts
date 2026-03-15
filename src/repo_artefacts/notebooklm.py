"""NotebookLM integration for uploading repo content and generating artefacts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple, TypeVar

from notebooklm import (
    Artifact,
    ArtifactType,
    AudioFormat,
    GenerationStatus,
    InfographicDetail,
    InfographicOrientation,
    NotebookLMClient,
    VideoStyle,
)
from notebooklm.exceptions import AuthError, RateLimitError, RPCError
from rich.table import Table

from repo_artefacts.console import get_console

T = TypeVar("T")

REAUTH_BACKOFF = [2, 10, 30]  # seconds between re-auth retries
RATE_LIMIT_BACKOFF = [30, 60, 300]  # escalating backoff for rate limits


# ---------------------------------------------------------------------------
# Type mapping — uses upstream notebooklm-py public ArtifactType (string enum)
# to stay in sync automatically.  No integer type codes.
# ---------------------------------------------------------------------------

# Our config keys → upstream ArtifactType string enum.
# Keys must match ARTEFACT_CONFIG (audio, video, slides, infographic).
NAME_TO_KIND: dict[str, ArtifactType] = {
    "audio": ArtifactType.AUDIO,
    "video": ArtifactType.VIDEO,
    "slides": ArtifactType.SLIDE_DECK,
    "infographic": ArtifactType.INFOGRAPHIC,
}

# Reverse: upstream ArtifactType → our config key name.
KIND_TO_NAME: dict[ArtifactType, str] = {v: k for k, v in NAME_TO_KIND.items()}


def _artifact_config_name(art: Artifact) -> str | None:
    """Map an upstream Artifact to our config key name, or None if unrecognised."""
    return KIND_TO_NAME.get(art.kind)


class DownloadSpec(NamedTuple):
    label: str
    list_method: str
    download_method: str
    filename: str


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ARTEFACT_CONFIG: dict[str, dict[str, str | None]] = {
    "audio": {
        "instructions": "Create an engaging audio overview of this codebase, explaining its architecture, key components, and how they work together",
        "method": "generate_audio",
    },
    "video": {
        "instructions": "Create a visual explainer of this codebase architecture and key workflows",
        "method": "generate_video",
    },
    "slides": {
        "instructions": "Create a presentation covering the codebase architecture, key components, and workflows",
        "method": "generate_slide_deck",
    },
    "infographic": {
        "instructions": None,
        "method": "generate_infographic",
    },
}

# Extra kwargs per artefact type (only for types that need them)
_GENERATE_KWARGS: dict[str, dict[str, object]] = {
    "audio": {"audio_format": AudioFormat.DEEP_DIVE},
    "video": {"video_style": VideoStyle.WHITEBOARD},
    "infographic": {
        "orientation": InfographicOrientation.LANDSCAPE,
        "detail_level": InfographicDetail.STANDARD,
    },
}


DOWNLOAD_MAP: list[DownloadSpec] = [
    DownloadSpec("audio", "list_audio", "download_audio", "audio_overview.mp3"),
    DownloadSpec("video", "list_video", "download_video", "video_overview.mp4"),
    DownloadSpec("slides", "list_slide_decks", "download_slide_deck", "slides.pdf"),
    DownloadSpec("infographic", "list_infographics", "download_infographic", "infographic.png"),
]


# ---------------------------------------------------------------------------
# Auth retry wrapper
# ---------------------------------------------------------------------------


async def _with_reauth(
    client: NotebookLMClient,
    fn: Callable[[], Awaitable[T]],
    label: str = "",
) -> T:
    """Run an async call, refreshing auth/CSRF tokens on RPC errors.

    Handles three failure modes:
    - AuthError: stale CSRF/session -> refresh_auth + quick retry
    - RateLimitError: throttled -> exponential backoff then refresh + retry
    - Other RPCError: transient server issue -> refresh + retry
    """
    last_exc: Exception | None = None
    backoffs = REAUTH_BACKOFF

    for attempt, wait in enumerate(backoffs, 1):
        try:
            return await fn()
        except RateLimitError as e:
            last_exc = e
            bk = RATE_LIMIT_BACKOFF[min(attempt - 1, len(RATE_LIMIT_BACKOFF) - 1)]
            get_console().print(
                f"[yellow]⚠[/yellow] {label} rate limited — "
                f"backoff {bk}s then re-auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(bk)
            await client.refresh_auth()
            get_console().print("[green]✓[/green] Auth refreshed after rate limit")
        except AuthError as e:
            last_exc = e
            get_console().print(
                f"[yellow]⚠[/yellow] {label} auth/CSRF expired — "
                f"refreshing (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()
            get_console().print("[green]✓[/green] Auth refreshed")
        except RPCError as e:
            last_exc = e
            get_console().print(
                f"[yellow]⚠[/yellow] {label} RPC error: {e} — "
                f"refreshing auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()

    # Final attempt after all backoffs exhausted
    try:
        return await fn()
    except RPCError as exc:
        raise (last_exc or RPCError(f"{label} failed after re-auth retries")) from exc  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def upload_repo(
    content_path: Path,
    repo_name: str,
    notebook_id: str | None = None,
) -> dict:
    """Upload collected repo content to a NotebookLM notebook.

    Checks for existing notebook with matching title before creating a new one.

    Returns:
        Dict with keys: id, title, source_replaced.
    """
    async with await NotebookLMClient.from_storage() as client:
        source_replaced = False
        if notebook_id:
            nb_id = notebook_id
            nb_title = repo_name
            get_console().print(f"Using existing notebook: [bold]{nb_id}[/bold]")
        else:
            notebooks = await _with_reauth(
                client, lambda: client.notebooks.list(), "list notebooks"
            )
            existing = next((nb for nb in notebooks if nb.title == repo_name), None)
            if existing:
                nb_id = existing.id
                nb_title = existing.title
                get_console().print(f"Found existing notebook: [bold]{nb_title}[/bold] ({nb_id})")
            else:
                notebook = await _with_reauth(
                    client,
                    lambda: client.notebooks.create(title=repo_name),
                    "create notebook",
                )
                nb_id = notebook.id
                nb_title = notebook.title
                get_console().print(f"Created notebook: [bold]{nb_title}[/bold] ({nb_id})")

        # Remove existing sources with the same name to prevent duplicates
        sources = await _with_reauth(client, lambda: client.sources.list(nb_id), "list sources")
        filename = content_path.name
        for src in sources:
            if src.title == filename:
                get_console().print(
                    f"  [dim]Replacing existing source: {src.title} ({src.id[:12]}...)[/dim]"
                )
                await _with_reauth(
                    client,
                    lambda sid=src.id: client.sources.delete(nb_id, sid),
                    "delete duplicate source",
                )
                source_replaced = True

        await _with_reauth(
            client,
            lambda: client.sources.add_file(nb_id, content_path, wait=True, wait_timeout=180.0),
            "upload source",
        )
        get_console().print(
            f"  [green]✓[/green] Uploaded {content_path.name} (ingestion complete)"
        )

    return {"id": nb_id, "title": nb_title, "source_replaced": source_replaced}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


async def _request_artefact(
    client: NotebookLMClient, notebook_id: str, artefact: str
) -> GenerationStatus:
    """Fire off a single generation request with re-auth on failure."""
    cfg = ARTEFACT_CONFIG[artefact]
    extra_kwargs = _GENERATE_KWARGS.get(artefact, {})

    async def _do() -> GenerationStatus:
        method_name = cfg["method"]
        assert method_name is not None
        method = getattr(client.artifacts, method_name)
        kwargs: dict[str, object] = {**extra_kwargs}
        if cfg["instructions"] is not None:
            kwargs["instructions"] = cfg["instructions"]
        return await method(notebook_id, **kwargs)

    return await _with_reauth(client, _do, artefact)


async def get_completed_artefacts(notebook_id: str) -> set[str]:
    """Return set of our config key names for completed artefacts in the notebook."""
    async with await NotebookLMClient.from_storage() as client:
        artifacts = await _with_reauth(
            client,
            lambda: client.artifacts.list(notebook_id),
            "check completed",
        )
    result: set[str] = set()
    for art in artifacts:
        if art.is_completed:
            name = _artifact_config_name(art)
            if name:
                result.add(name)
    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def download_artefacts(notebook_id: str, output_dir: Path) -> None:
    """Download all available artefacts from a notebook."""
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await NotebookLMClient.from_storage() as client:
        for label, list_method, dl_method, filename in DOWNLOAD_MAP:
            items = await _with_reauth(
                client,
                lambda lm=list_method: getattr(client.artifacts, lm)(notebook_id),
                f"list {label}",
            )
            if not items:
                continue
            # Skip failed artefacts
            ready = [i for i in items if i.is_completed]
            if not ready:
                get_console().print(
                    f"[yellow]⚠[/yellow] {label}: exists but not ready (failed or processing), skipping"
                )
                continue
            if len(ready) == 1:
                path = str(output_dir / filename)
                await _with_reauth(
                    client,
                    lambda dm=dl_method, p=path, aid=ready[0].id: getattr(client.artifacts, dm)(
                        notebook_id, p, artifact_id=aid
                    ),
                    f"download {label}",
                )
                get_console().print(f"[green]✓[/green] Downloaded {path}")
            else:
                stem, ext = filename.rsplit(".", 1)
                for i, artifact in enumerate(ready, 1):
                    path = str(output_dir / f"{stem}_{i:02d}.{ext}")
                    await _with_reauth(
                        client,
                        lambda dm=dl_method, p=path, aid=artifact.id: getattr(
                            client.artifacts, dm
                        )(notebook_id, p, artifact_id=aid),
                        f"download {label}",
                    )
                    get_console().print(f"[green]✓[/green] Downloaded {path}")

    get_console().print(f"[bold green]Done.[/bold green] Files saved to {output_dir}")


# ---------------------------------------------------------------------------
# Notebook management
# ---------------------------------------------------------------------------


async def list_notebooks() -> None:
    """List all NotebookLM notebooks with source counts."""
    async with await NotebookLMClient.from_storage() as client:
        notebooks = await _with_reauth(client, lambda: client.notebooks.list(), "list notebooks")
        rows = []
        for nb in notebooks:
            sources = await _with_reauth(
                client, lambda nid=nb.id: client.sources.list(nid), "list sources"
            )
            rows.append((nb.id, nb.title, str(len(sources))))

    table = Table(title="Notebooks")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Sources", justify="right")
    for row in rows:
        table.add_row(*row)
    get_console().print(table)


async def list_sources(notebook_id: str) -> None:
    """List all sources in a specific notebook."""
    async with await NotebookLMClient.from_storage() as client:
        sources = await _with_reauth(
            client, lambda: client.sources.list(notebook_id), "list sources"
        )

    table = Table(title=f"Sources in {notebook_id}")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="bold")
    for i, src in enumerate(sources, 1):
        table.add_row(str(i), src.id, src.title)
    get_console().print(table)


async def delete_notebook(notebook_id: str) -> None:
    """Delete a notebook and all its contents."""
    async with await NotebookLMClient.from_storage() as client:
        await _with_reauth(client, lambda: client.notebooks.delete(notebook_id), "delete notebook")
        get_console().print(f"[green]✓[/green] Deleted notebook {notebook_id}")
