"""NotebookLM integration for uploading repo content and generating artefacts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from notebooklm import AudioFormat, GenerationStatus, NotebookLMClient, VideoStyle
from notebooklm.exceptions import AuthError, RateLimitError, RPCError
from rich.console import Console
from rich.table import Table

console = Console()

T = TypeVar("T")

REAUTH_BACKOFF = [2, 10, 30]  # seconds between re-auth retries
RATE_LIMIT_BACKOFF = [30, 60, 300]  # escalating backoff for rate limits

# Strings in GenerationStatus.error that indicate daily quota exhaustion
# (not transient — retrying won't help until the 24h reset)
QUOTA_ERROR_PATTERNS = ["rate limit", "quota exceeded", "quota"]


async def _with_reauth(
    client: NotebookLMClient,
    fn: Callable[[], Awaitable[T]],
    label: str = "",
) -> T:
    """Run an async call, refreshing auth/CSRF tokens on RPC errors.

    Handles three failure modes:
    - AuthError: stale CSRF/session → refresh_auth + quick retry
    - RateLimitError: throttled → exponential backoff then refresh + retry
    - Other RPCError: transient server issue → refresh + retry
    """
    last_exc: Exception | None = None
    backoffs = REAUTH_BACKOFF

    for attempt, wait in enumerate(backoffs, 1):
        try:
            return await fn()
        except RateLimitError as e:
            last_exc = e
            bk = RATE_LIMIT_BACKOFF[min(attempt - 1, len(RATE_LIMIT_BACKOFF) - 1)]
            console.print(
                f"[yellow]⚠[/yellow] {label} rate limited — "
                f"backoff {bk}s then re-auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(bk)
            await client.refresh_auth()
            console.print("[green]✓[/green] Auth refreshed after rate limit")
        except AuthError as e:
            last_exc = e
            console.print(
                f"[yellow]⚠[/yellow] {label} auth/CSRF expired — "
                f"refreshing (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()
            console.print("[green]✓[/green] Auth refreshed")
        except RPCError as e:
            last_exc = e
            console.print(
                f"[yellow]⚠[/yellow] {label} RPC error: {e} — "
                f"refreshing auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()

    # Final attempt after all backoffs exhausted
    try:
        return await fn()
    except RPCError:
        raise last_exc or RPCError(f"{label} failed after re-auth retries")  # type: ignore[call-arg]


def _is_quota_error(error_msg: str) -> bool:
    """Check if an error message indicates daily quota exhaustion."""
    lower = error_msg.lower()
    return any(p in lower for p in QUOTA_ERROR_PATTERNS)


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
            notebooks = await _with_reauth(
                client, lambda: client.notebooks.list(), "list notebooks"
            )
            existing = next((nb for nb in notebooks if nb.title == repo_name), None)
            if existing:
                nb_id = existing.id
                nb_title = existing.title
                console.print(
                    f"Found existing notebook: [bold]{nb_title}[/bold] ({nb_id})"
                )
            else:
                notebook = await _with_reauth(
                    client,
                    lambda: client.notebooks.create(title=repo_name),
                    "create notebook",
                )
                nb_id = notebook.id
                nb_title = notebook.title
                console.print(f"Created notebook: [bold]{nb_title}[/bold] ({nb_id})")

        await _with_reauth(
            client,
            lambda: client.sources.add_file(nb_id, content_path),
            "upload source",
        )
        console.print(f"  [green]✓[/green] Uploaded {content_path.name}")

    return {"id": nb_id, "title": nb_title}


MAX_RETRIES = 3


async def _request_artefact(
    client: NotebookLMClient, notebook_id: str, artefact: str
) -> GenerationStatus:
    """Fire off a single generation request with re-auth on failure."""
    cfg = ARTEFACT_CONFIG[artefact]

    async def _do() -> GenerationStatus:
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

    return await _with_reauth(client, _do, artefact)


ARTEFACT_TYPE_CODE = {"audio": 1, "video": 8, "slides": 3, "infographic": 7}


async def _delete_failed_by_type(
    client: NotebookLMClient, notebook_id: str, artefact: str
) -> None:
    """Delete any failed artefacts of the given type (required before retry)."""
    type_code = ARTEFACT_TYPE_CODE[artefact]
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), f"list {artefact}"
    )
    for art in raw:
        if len(art) > 4 and art[2] == type_code and art[4] == 4:  # FAILED
            console.print(f"  [dim]Deleting failed {artefact} ({art[0][:12]}...)[/dim]")
            await _with_reauth(
                client,
                lambda aid=art[0]: client.artifacts.delete(notebook_id, aid),
                f"delete {artefact}",
            )


async def _snapshot_artefact_ids(
    client: NotebookLMClient, notebook_id: str
) -> dict[str, set[str]]:
    """Snapshot existing artefact IDs by type name."""
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), "snapshot artefacts"
    )
    code_to_name = {v: k for k, v in ARTEFACT_TYPE_CODE.items()}
    result: dict[str, set[str]] = {name: set() for name in ARTEFACT_TYPE_CODE}
    for art in raw:
        if len(art) > 2:
            name = code_to_name.get(art[2])
            if name:
                result[name].add(art[0])
    return result


async def get_completed_artefacts(notebook_id: str) -> set[str]:
    """Return set of artefact type names that are already completed in the notebook."""
    async with await NotebookLMClient.from_storage() as client:
        raw = await _with_reauth(
            client,
            lambda: client.artifacts._list_raw(notebook_id),
            "check completed",
        )
    code_to_name = {v: k for k, v in ARTEFACT_TYPE_CODE.items()}
    completed: set[str] = set()
    for art in raw:
        if len(art) > 4 and art[4] == 3:  # COMPLETED
            name = code_to_name.get(art[2])
            if name:
                completed.add(name)
    return completed


async def _poll_by_type(
    client: NotebookLMClient,
    notebook_id: str,
    artefact: str,
    known_ids: set[str],
) -> str:
    """Find a new or changed artefact of the given type. Returns status string."""
    type_code = ARTEFACT_TYPE_CODE[artefact]
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), f"poll {artefact}"
    )
    for art in raw:
        if len(art) > 4 and art[2] == type_code:
            status_code = art[4]
            if art[0] not in known_ids or status_code in (3, 4):
                # New artefact or status changed
                if status_code == 3:
                    return "completed"
                elif status_code == 4:
                    return "failed"
                elif status_code in (1, 2):
                    return "in_progress"
    return "in_progress"


async def generate_artefacts(
    notebook_id: str, artefacts: list[str], timeout: int = 900
) -> None:
    """Generate requested artefact types concurrently with retry on failure.

    Handles three failure modes:
    - Daily quota exhaustion (infographics/slides have stricter caps) → bail early
    - Stale auth/CSRF → refresh_auth + retry
    - Transient RPC errors → backoff + retry

    Polls by artefact type (not task_id) because the NotebookLM API returns
    different IDs for generation tasks vs completed artefacts.
    """
    async with await NotebookLMClient.from_storage() as client:
        # Snapshot existing artefacts so we can detect new completions
        before = await _snapshot_artefact_ids(client, notebook_id)
        pending: set[str] = set()
        retries: dict[str, int] = {a: 0 for a in artefacts}
        quota_exhausted: set[str] = set()

        for artefact in artefacts:
            console.print(f"[blue]⏳[/blue] Requesting {artefact}...")
            try:
                await _delete_failed_by_type(client, notebook_id, artefact)
                status = await _request_artefact(client, notebook_id, artefact)
                if status.is_failed or not status.task_id:
                    err = status.error or "no task_id"
                    if _is_quota_error(err):
                        # Refresh auth and try once more to distinguish
                        # quota exhaustion from stale CSRF
                        console.print(
                            f"[yellow]⚠[/yellow] {artefact} rejected ({err})"
                            " — refreshing auth to confirm..."
                        )
                        await client.refresh_auth()
                        await asyncio.sleep(5)
                        status = await _request_artefact(client, notebook_id, artefact)
                        if status.is_failed or not status.task_id:
                            quota_exhausted.add(artefact)
                            console.print(
                                f"[red]✗[/red] {artefact}: daily quota exhausted"
                                " (NotebookLM caps infographics/slides"
                                " separately). Retry after 24h reset."
                            )
                            continue
                        else:
                            # Auth refresh fixed it — was stale CSRF
                            pending.add(artefact)
                            continue
                    retries[artefact] += 1
                    console.print(
                        f"[yellow]⚠[/yellow] {artefact} failed immediately"
                        f" ({err})"
                        f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                    )
                    await client.refresh_auth()
                else:
                    pending.add(artefact)
            except Exception as e:
                retries[artefact] += 1
                console.print(
                    f"[yellow]⚠[/yellow] Failed to request {artefact}: {e}"
                    f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                )

        # Queue initial failures for retry (excluding quota-exhausted)
        needs_retry = [
            a
            for a in artefacts
            if a not in pending
            and a not in quota_exhausted
            and retries[a] <= MAX_RETRIES
        ]

        elapsed = 0
        poll_interval = 30

        console.print(
            f"[dim]Timeout: {timeout}s ({timeout // 60}min),"
            f" max retries: {MAX_RETRIES}[/dim]"
        )

        while (pending or needs_retry) and elapsed < timeout:
            # Retry any queued failures — refresh auth + backoff first
            for label in list(needs_retry):
                backoff = RATE_LIMIT_BACKOFF[
                    min(retries[label] - 1, len(RATE_LIMIT_BACKOFF) - 1)
                ]
                console.print(
                    f"[blue]⏳[/blue] Retrying {label}"
                    f" ({retries[label]}/{MAX_RETRIES})"
                    f" — backoff {backoff}s + auth refresh..."
                )
                await asyncio.sleep(backoff)
                await client.refresh_auth()
                console.print("[green]✓[/green] Auth refreshed")
                try:
                    await _delete_failed_by_type(client, notebook_id, label)
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
                        pending.add(label)
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

            # Poll by artefact type (not task_id — they don't match)
            for label in list(pending):
                try:
                    status_str = await _poll_by_type(
                        client, notebook_id, label, before.get(label, set())
                    )
                except Exception as e:
                    console.print(
                        f"[yellow]⚠[/yellow] Poll error for {label}: {e}"
                        " — refreshing auth"
                    )
                    await client.refresh_auth()
                    continue

                if status_str == "completed":
                    console.print(f"[green]✓[/green] {label.capitalize()} ready")
                    pending.discard(label)
                elif status_str == "failed":
                    pending.discard(label)
                    retries[label] += 1
                    if retries[label] <= MAX_RETRIES:
                        console.print(
                            f"[yellow]⚠[/yellow] {label} failed"
                            f" — queued retry ({retries[label]}/{MAX_RETRIES})"
                        )
                        needs_retry.append(label)
                    else:
                        console.print(
                            f"[red]✗[/red] {label} failed after {MAX_RETRIES} retries"
                        )
                else:
                    console.print(
                        f"[dim]  … {label} still generating ({elapsed}s)[/dim]"
                    )

        for label in list(pending) + needs_retry:
            console.print(f"[red]✗[/red] {label.capitalize()} timed out")

        if quota_exhausted:
            console.print(
                f"\n[yellow]ℹ[/yellow] Quota-limited artefacts: "
                f"[bold]{', '.join(sorted(quota_exhausted))}[/bold]"
                "\n  NotebookLM enforces separate daily caps for"
                " infographics (~20-25/day Pro) and slides."
                "\n  These reset 24h from first daily use (UTC)."
                "\n  Retry tomorrow: repo-artefacts generate"
                f" {''.join(f' --{a}' for a in sorted(quota_exhausted))}"
            )

    console.print("[bold green]Done.[/bold green]")


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
                console.print(
                    f"[yellow]⚠[/yellow] {label}: exists but not ready (failed or processing), skipping"
                )
                continue
            if len(ready) == 1:
                path = str(output_dir / filename)
                await _with_reauth(
                    client,
                    lambda dm=dl_method, p=path, aid=ready[0].id: getattr(
                        client.artifacts, dm
                    )(notebook_id, p, artifact_id=aid),
                    f"download {label}",
                )
                console.print(f"[green]✓[/green] Downloaded {path}")
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
                    console.print(f"[green]✓[/green] Downloaded {path}")

    console.print(f"[bold green]Done.[/bold green] Files saved to {output_dir}")


async def list_notebooks() -> None:
    """List all NotebookLM notebooks with source counts."""
    async with await NotebookLMClient.from_storage() as client:
        notebooks = await _with_reauth(
            client, lambda: client.notebooks.list(), "list notebooks"
        )
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
    console.print(table)


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
    console.print(table)


async def delete_notebook(notebook_id: str) -> None:
    """Delete a notebook and all its contents."""
    async with await NotebookLMClient.from_storage() as client:
        await _with_reauth(
            client, lambda: client.notebooks.delete(notebook_id), "delete notebook"
        )
        console.print(f"[green]✓[/green] Deleted notebook {notebook_id}")
