"""NotebookLM integration for uploading repo content and generating artefacts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import NamedTuple, TypeVar

from notebooklm import AudioFormat, GenerationStatus, NotebookLMClient, VideoStyle
from notebooklm.exceptions import AuthError, RateLimitError, RPCError
from rich.table import Table

from repo_artefacts.console import get_console

T = TypeVar("T")

REAUTH_BACKOFF = [2, 10, 30]  # seconds between re-auth retries
RATE_LIMIT_BACKOFF = [30, 60, 300]  # escalating backoff for rate limits

# Strings in GenerationStatus.error that indicate daily quota exhaustion
# (not transient — retrying won't help until the 24h reset)
QUOTA_ERROR_PATTERNS = ["rate limit", "quota exceeded", "quota"]


# ---------------------------------------------------------------------------
# Type definitions for NotebookLM API data
# ---------------------------------------------------------------------------


class ArtefactStatus(IntEnum):
    """Status codes from the NotebookLM _list_raw API."""

    QUEUED = 1
    IN_PROGRESS = 2
    COMPLETED = 3
    FAILED = 4


class ArtefactType(IntEnum):
    """Type codes from the NotebookLM _list_raw API."""

    AUDIO = 1
    SLIDES = 3
    INFOGRAPHIC = 7
    VIDEO = 8


# Mapping from artefact name to ArtefactType
NAME_TO_TYPE: dict[str, ArtefactType] = {t.name.lower(): t for t in ArtefactType}


@dataclass(frozen=True, slots=True)
class RawArtefact:
    """Parsed representation of a single _list_raw entry."""

    id: str
    type_code: ArtefactType
    status: ArtefactStatus

    @classmethod
    def from_raw(cls, arr: list) -> RawArtefact | None:
        """Parse a raw API array. Returns None if too short or unknown codes."""
        if len(arr) <= 4:
            return None
        try:
            return cls(
                id=str(arr[0]),
                type_code=ArtefactType(arr[2]),
                status=ArtefactStatus(arr[4]),
            )
        except ValueError:
            return None

    @property
    def is_completed(self) -> bool:
        return self.status == ArtefactStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == ArtefactStatus.FAILED

    @property
    def type_name(self) -> str:
        """Lowercase name matching ARTEFACT_CONFIG keys."""
        return self.type_code.name.lower()


def _parse_raw_artefacts(raw: list) -> list[RawArtefact]:
    """Parse all raw arrays, skipping entries that are too short or unknown."""
    results = []
    for arr in raw:
        art = RawArtefact.from_raw(arr)
        if art is not None:
            results.append(art)
    return results


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
}


@dataclass
class GenerateResult:
    """Outcome of a generate_artefacts() call."""

    completed: set[str]
    failed: set[str]
    quota_exhausted: set[str]


DOWNLOAD_MAP: list[DownloadSpec] = [
    DownloadSpec("audio", "list_audio", "download_audio", "audio_overview.mp3"),
    DownloadSpec("video", "list_video", "download_video", "video_overview.mp4"),
    DownloadSpec("slides", "list_slide_decks", "download_slide_deck", "slides.pdf"),
    DownloadSpec("infographic", "list_infographics", "download_infographic", "infographic.png"),
]

MAX_RETRIES = 3


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


def _is_quota_error(error_msg: str) -> bool:
    """Check if an error message indicates daily quota exhaustion."""
    lower = error_msg.lower()
    return any(p in lower for p in QUOTA_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


async def upload_repo(
    content_path: Path,
    repo_name: str,
    notebook_id: str | None = None,
) -> dict[str, str]:
    """Upload collected repo content to a NotebookLM notebook.

    Checks for existing notebook with matching title before creating a new one.

    Returns:
        Dict with keys: id, title.
    """
    async with await NotebookLMClient.from_storage() as client:
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

        await _with_reauth(
            client,
            lambda: client.sources.add_file(nb_id, content_path),
            "upload source",
        )
        get_console().print(f"  [green]✓[/green] Uploaded {content_path.name}")

    return {"id": nb_id, "title": nb_title}


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


async def _delete_existing_by_type(
    client: NotebookLMClient,
    notebook_id: str,
    artefact: str,
    *,
    failed_only: bool = False,
) -> None:
    """Delete artefacts of the given type before (re)generation.

    Args:
        failed_only: If True, only delete FAILED artefacts (used during retry).
            If False, delete ALL artefacts of this type including completed
            (used when explicitly requesting regeneration).
    """
    artefact_type = NAME_TO_TYPE[artefact]
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), f"list {artefact}"
    )
    parsed = _parse_raw_artefacts(raw)

    # Log what we see for debugging when entries are skipped
    if not parsed and raw:
        get_console().print(
            f"  [dim]⚠ {artefact}: {len(raw)} raw entries but none parseable"
            f" (first entry has {len(raw[0]) if raw[0] else 0} elements)[/dim]"
        )
    for art in parsed:
        if art.type_code != artefact_type:
            continue
        should_delete = art.is_failed or not failed_only
        if should_delete:
            get_console().print(
                f"  [dim]Deleting {art.status.name.lower()} {artefact} ({art.id[:12]}...)[/dim]"
            )
            await _with_reauth(
                client,
                lambda aid=art.id: client.artifacts.delete(notebook_id, aid),
                f"delete {artefact}",
            )
        else:
            get_console().print(
                f"  [dim]{artefact}: found existing with status"
                f" {art.status.name} ({art.id[:12]}...)[/dim]"
            )


async def _snapshot_artefact_ids(
    client: NotebookLMClient, notebook_id: str
) -> dict[str, set[str]]:
    """Snapshot existing artefact IDs by type name."""
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), "snapshot artefacts"
    )
    result: dict[str, set[str]] = {name: set() for name in ARTEFACT_CONFIG}
    for art in _parse_raw_artefacts(raw):
        if art.type_name in result:
            result[art.type_name].add(art.id)
    return result


async def get_completed_artefacts(notebook_id: str) -> set[str]:
    """Return set of artefact type names that are already completed in the notebook."""
    async with await NotebookLMClient.from_storage() as client:
        raw = await _with_reauth(
            client,
            lambda: client.artifacts._list_raw(notebook_id),
            "check completed",
        )
    return {art.type_name for art in _parse_raw_artefacts(raw) if art.is_completed}


async def _poll_by_type(
    client: NotebookLMClient,
    notebook_id: str,
    artefact: str,
    known_ids: set[str],
) -> str:
    """Find a new or changed artefact of the given type. Returns status string."""
    artefact_type = NAME_TO_TYPE[artefact]
    raw = await _with_reauth(
        client, lambda: client.artifacts._list_raw(notebook_id), f"poll {artefact}"
    )
    for art in _parse_raw_artefacts(raw):
        if art.type_code != artefact_type:
            continue
        if art.id not in known_ids or art.status in (
            ArtefactStatus.COMPLETED,
            ArtefactStatus.FAILED,
        ):
            if art.is_completed:
                return "completed"
            if art.is_failed:
                return "failed"
            if art.status in (ArtefactStatus.QUEUED, ArtefactStatus.IN_PROGRESS):
                return "in_progress"
    return "in_progress"


async def generate_artefacts(
    notebook_id: str, artefacts: list[str], timeout: int = 900
) -> GenerateResult:
    """Generate requested artefact types with retry on failure.

    Handles three failure modes:
    - Daily quota exhaustion (infographics/slides have stricter caps) -> bail early
    - Stale auth/CSRF -> refresh_auth + retry
    - Transient RPC errors -> backoff + retry

    Polls by artefact type (not task_id) because the NotebookLM API returns
    different IDs for generation tasks vs completed artefacts.
    """
    async with await NotebookLMClient.from_storage() as client:
        # Snapshot existing artefacts so we can detect new completions
        before = await _snapshot_artefact_ids(client, notebook_id)
        pending: set[str] = set()
        retries: dict[str, int] = {a: 0 for a in artefacts}
        quota_exhausted: set[str] = set()
        completed: set[str] = set()
        permanently_failed: set[str] = set()

        for artefact in artefacts:
            get_console().print(f"[blue]⏳[/blue] Requesting {artefact}...")
            try:
                await _delete_existing_by_type(client, notebook_id, artefact)
                status = await _request_artefact(client, notebook_id, artefact)
                if status.is_failed or not status.task_id:
                    err = status.error or "no artifact_id returned"
                    err_detail = (
                        f"error={status.error!r}, error_code={status.error_code!r},"
                        f" task_id={status.task_id!r}, status={status.status!r},"
                        f" metadata={status.metadata!r}"
                    )
                    if _is_quota_error(err):
                        # Refresh auth and try once more to distinguish
                        # quota exhaustion from stale CSRF
                        get_console().print(
                            f"[yellow]⚠[/yellow] {artefact} rejected ({err})"
                            " — refreshing auth to confirm..."
                        )
                        await client.refresh_auth()
                        await asyncio.sleep(5)
                        status = await _request_artefact(client, notebook_id, artefact)
                        if status.is_failed or not status.task_id:
                            quota_exhausted.add(artefact)
                            get_console().print(
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
                    get_console().print(
                        f"[yellow]⚠[/yellow] {artefact} failed immediately"
                        f" ({err})"
                        f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                    )
                    get_console().print(f"  [dim]Detail: {err_detail}[/dim]")
                    await client.refresh_auth()
                else:
                    pending.add(artefact)
            except Exception as e:
                retries[artefact] += 1
                get_console().print(
                    f"[yellow]⚠[/yellow] Failed to request {artefact}: {e}"
                    f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                )

        # Queue initial failures for retry (excluding quota-exhausted)
        needs_retry = [
            a
            for a in artefacts
            if a not in pending and a not in quota_exhausted and retries[a] <= MAX_RETRIES
        ]

        start_time = time.monotonic()
        deadline = start_time + timeout
        poll_interval = 30

        get_console().print(
            f"[dim]Timeout: {timeout}s ({timeout // 60}min), max retries: {MAX_RETRIES}[/dim]"
        )

        while (pending or needs_retry) and time.monotonic() < deadline:
            # Retry any queued failures — refresh auth + backoff first
            for label in list(needs_retry):
                backoff = RATE_LIMIT_BACKOFF[min(retries[label] - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                get_console().print(
                    f"[blue]⏳[/blue] Retrying {label}"
                    f" ({retries[label]}/{MAX_RETRIES})"
                    f" — backoff {backoff}s + auth refresh..."
                )
                await asyncio.sleep(backoff)
                await client.refresh_auth()
                get_console().print("[green]✓[/green] Auth refreshed")
                try:
                    await _delete_existing_by_type(client, notebook_id, label, failed_only=True)
                    status = await _request_artefact(client, notebook_id, label)
                    if status.is_failed or not status.task_id:
                        retries[label] += 1
                        if retries[label] > MAX_RETRIES:
                            get_console().print(
                                f"[red]✗[/red] {label} failed after"
                                f" {MAX_RETRIES} retries: {status.error}"
                            )
                            permanently_failed.add(label)
                            needs_retry.remove(label)
                        # else stays in needs_retry for next loop
                    else:
                        pending.add(label)
                        needs_retry.remove(label)
                except Exception as e:
                    retries[label] += 1
                    if retries[label] > MAX_RETRIES:
                        get_console().print(
                            f"[red]✗[/red] {label} failed after {MAX_RETRIES} retries: {e}"
                        )
                        permanently_failed.add(label)
                        needs_retry.remove(label)

            if not pending and not needs_retry:
                break

            await asyncio.sleep(poll_interval)

            # Poll by artefact type (not task_id — they don't match)
            elapsed = int(time.monotonic() - start_time)
            for label in list(pending):
                try:
                    status_str = await _poll_by_type(
                        client, notebook_id, label, before.get(label, set())
                    )
                except Exception as e:
                    get_console().print(
                        f"[yellow]⚠[/yellow] Poll error for {label}: {e} — refreshing auth"
                    )
                    await client.refresh_auth()
                    continue

                if status_str == "completed":
                    get_console().print(f"[green]✓[/green] {label.capitalize()} ready")
                    completed.add(label)
                    pending.discard(label)
                elif status_str == "failed":
                    pending.discard(label)
                    retries[label] += 1
                    if retries[label] <= MAX_RETRIES:
                        get_console().print(
                            f"[yellow]⚠[/yellow] {label} failed"
                            f" — queued retry ({retries[label]}/{MAX_RETRIES})"
                        )
                        if label not in needs_retry:
                            needs_retry.append(label)
                    else:
                        get_console().print(
                            f"[red]✗[/red] {label} failed after {MAX_RETRIES} retries"
                        )
                        permanently_failed.add(label)
                else:
                    get_console().print(f"[dim]  … {label} still generating ({elapsed}s)[/dim]")

        timed_out: set[str] = set()
        for label in list(pending) + needs_retry:
            get_console().print(f"[red]✗[/red] {label.capitalize()} timed out")
            timed_out.add(label)

        if quota_exhausted:
            get_console().print(
                f"\n[yellow]i[/yellow] Quota-limited artefacts: "
                f"[bold]{', '.join(sorted(quota_exhausted))}[/bold]"
                "\n  NotebookLM enforces separate daily caps for"
                " infographics (~20-25/day Pro) and slides."
                "\n  These reset 24h from first daily use (UTC)."
                "\n  Retry tomorrow: repo-artefacts generate"
                f" {''.join(f' --{a}' for a in sorted(quota_exhausted))}"
            )

    get_console().print("[bold green]Done.[/bold green]")
    return GenerateResult(
        completed=completed,
        failed=permanently_failed | timed_out,
        quota_exhausted=quota_exhausted,
    )


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
