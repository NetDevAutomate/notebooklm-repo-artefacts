"""NotebookLM integration for uploading repo content and generating artefacts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from notebooklm import (
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

# Strings in GenerationStatus.error that indicate daily quota exhaustion
# (not transient — retrying won't help until the 24h reset)
QUOTA_ERROR_PATTERNS = ["rate limit", "quota exceeded", "quota"]


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

# Map artefact type name to upstream ArtifactType str enum
NAME_TO_ARTIFACT_TYPE: dict[str, ArtifactType] = {
    "audio": ArtifactType.AUDIO,
    "video": ArtifactType.VIDEO,
    "slides": ArtifactType.SLIDE_DECK,
    "infographic": ArtifactType.INFOGRAPHIC,
}


@dataclass
class GenerateResult:
    """Outcome of a generate_artefacts() call."""

    completed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    quota_exhausted: set[str] = field(default_factory=set)


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
) -> dict[str, str | bool]:
    """Upload collected repo content to a NotebookLM notebook.

    If notebook_id is provided (resume/override), uses it directly.
    Otherwise, deletes any existing notebook with matching title and
    creates a fresh one to avoid stale artefact duplicates.

    Returns:
        Dict with keys: id, title, source_replaced.
    """
    async with await NotebookLMClient.from_storage() as client:
        source_replaced = False
        if notebook_id:
            # Resume or explicit override — use existing notebook as-is
            nb_id = notebook_id
            nb_title = repo_name
            get_console().print(f"Using existing notebook: [bold]{nb_id}[/bold]")
        else:
            # Fresh run: delete any existing notebook with this title
            # to avoid stale artefact duplicates
            notebooks = await _with_reauth(
                client, lambda: client.notebooks.list(), "list notebooks"
            )
            existing = next((nb for nb in notebooks if nb.title == repo_name), None)
            if existing:
                old_id = existing.id
                get_console().print(
                    f"  [dim]Deleting existing notebook: {existing.title} ({old_id})[/dim]"
                )
                await _with_reauth(
                    client,
                    lambda: client.notebooks.delete(old_id),
                    "delete old notebook",
                )
                # Give the API time to process the deletion
                await asyncio.sleep(2)
                # Verify it's actually gone
                notebooks = await _with_reauth(
                    client, lambda: client.notebooks.list(), "verify deletion"
                )
                still_there = any(nb.id == old_id for nb in notebooks)
                if still_there:
                    get_console().print(
                        "[yellow]⚠[/yellow] Notebook still exists after delete — retrying"
                    )
                    await _with_reauth(
                        client,
                        lambda: client.notebooks.delete(old_id),
                        "delete old notebook (retry)",
                    )
                    await asyncio.sleep(3)

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


async def _delete_existing_by_type(
    client: NotebookLMClient,
    notebook_id: str,
    artefact: str,
    *,
    failed_only: bool = False,
) -> None:
    """Delete artefacts of the given type before (re)generation.

    Uses upstream client.artifacts.list() with type filtering instead of
    _list_raw() + manual parsing.

    Args:
        failed_only: If True, only delete FAILED artefacts (used during retry).
            If False, delete ALL artefacts of this type including completed
            (used when explicitly requesting regeneration).
    """
    artifact_type = NAME_TO_ARTIFACT_TYPE[artefact]
    artifacts = await _with_reauth(
        client,
        lambda: client.artifacts.list(notebook_id, artifact_type=artifact_type),
        f"list {artefact}",
    )

    for art in artifacts:
        should_delete = art.is_failed or not failed_only
        if should_delete:
            status_label = "failed" if art.is_failed else "completed"
            get_console().print(
                f"  [dim]Deleting {status_label} {artefact} ({art.id[:12]}...)[/dim]"
            )
            await _with_reauth(
                client,
                lambda aid=art.id: client.artifacts.delete(notebook_id, aid),
                f"delete {artefact}",
            )
        else:
            get_console().print(
                f"  [dim]{artefact}: found existing completed ({art.id[:12]}...)[/dim]"
            )


async def get_completed_artefacts(notebook_id: str) -> set[str]:
    """Return set of artefact type names that are already completed in the notebook.

    Uses upstream client.artifacts.list() with .is_completed property.
    """
    async with await NotebookLMClient.from_storage() as client:
        artifacts = await _with_reauth(
            client,
            lambda: client.artifacts.list(notebook_id),
            "check completed",
        )
    return {
        art.kind.value
        for art in artifacts
        if art.is_completed and art.kind.value in ARTEFACT_CONFIG
    }


async def _wait_for_artefact(
    client: NotebookLMClient,
    notebook_id: str,
    task_id: str,
    timeout: float,
    label: str,
) -> GenerationStatus:
    """Wait for a single artefact generation to complete.

    Uses upstream wait_for_completion() which has:
    - Exponential backoff (2s → 10s)
    - Media-readiness checks (won't report COMPLETED until URLs are populated)
    - Proper timeout handling
    """
    return await _with_reauth(
        client,
        lambda: client.artifacts.wait_for_completion(
            notebook_id,
            task_id,
            initial_interval=2.0,
            max_interval=10.0,
            timeout=timeout,
        ),
        f"wait {label}",
    )


async def _deduplicate_sources(client: NotebookLMClient, notebook_id: str) -> None:
    """Check for duplicate sources and remove extras, keeping only the newest of each title."""
    sources = await _with_reauth(client, lambda: client.sources.list(notebook_id), "list sources")
    # Group by title
    by_title: dict[str, list] = {}
    for src in sources:
        title = src.title or "(untitled)"
        by_title.setdefault(title, []).append(src)

    for title, group in by_title.items():
        if len(group) <= 1:
            continue
        # Keep the last one (most recently added), delete the rest
        duplicates = group[:-1]
        get_console().print(
            f"  [yellow]⚠[/yellow] Found {len(group)} sources named '{title}'"
            f" — removing {len(duplicates)} duplicate(s)"
        )
        for dup in duplicates:
            await _with_reauth(
                client,
                lambda sid=dup.id: client.sources.delete(notebook_id, sid),
                f"delete duplicate source {dup.id[:12]}",
            )


async def generate_artefacts(
    notebook_id: str,
    artefacts: list[str],
    timeout: int = 900,
    *,
    force_regen: bool = False,
) -> GenerateResult:
    """Generate requested artefact types with retry on failure.

    Handles three failure modes:
    - Daily quota exhaustion (infographics/slides have stricter caps) -> bail early
    - Stale auth/CSRF -> refresh_auth + retry
    - Transient RPC errors -> backoff + retry

    Uses upstream wait_for_completion() for polling, which provides:
    - Exponential backoff (2s → 10s)
    - Media-readiness checks (won't report COMPLETED until URLs populated)
    - Proper timeout handling

    Args:
        force_regen: If True, delete ALL existing artefacts of each type before
            regenerating. If False (default), only delete FAILED artefacts and
            skip types that are already completed.
    """
    async with await NotebookLMClient.from_storage() as client:
        # Pre-check: remove duplicate sources to avoid confused generation
        await _deduplicate_sources(client, notebook_id)

        # Check what's already completed (skip unless force_regen)
        already_completed: set[str] = set()
        if not force_regen:
            already_completed = await get_completed_artefacts(notebook_id)
            if already_completed:
                get_console().print(
                    f"  Already completed: [green]{', '.join(sorted(already_completed))}[/green]"
                )

        # Filter out already-completed types
        to_generate = [a for a in artefacts if a not in already_completed]
        if not to_generate:
            get_console().print(
                "[green]All artefacts already completed, skipping generation[/green]"
            )
            return GenerateResult(
                completed=set(artefacts),
                failed=set(),
                quota_exhausted=set(),
            )

        pending: dict[str, str] = {}  # artefact_name -> task_id
        retries: dict[str, int] = {a: 0 for a in to_generate}
        quota_exhausted: set[str] = set()
        completed: set[str] = set()
        permanently_failed: set[str] = set()

        for artefact in to_generate:
            get_console().print(f"[blue]⏳[/blue] Requesting {artefact}...")
            try:
                # Only delete failed artefacts unless force_regen is set
                await _delete_existing_by_type(
                    client, notebook_id, artefact, failed_only=not force_regen
                )
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
                            pending[artefact] = status.task_id
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
                    pending[artefact] = status.task_id
            except Exception as e:
                retries[artefact] += 1
                get_console().print(
                    f"[yellow]⚠[/yellow] Failed to request {artefact}: {e}"
                    f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                )

        # Queue initial failures for retry (excluding quota-exhausted)
        needs_retry = [
            a
            for a in to_generate
            if a not in pending and a not in quota_exhausted and retries[a] <= MAX_RETRIES
        ]

        start_time = time.monotonic()
        deadline = start_time + timeout

        get_console().print(
            f"[dim]Timeout: {timeout}s ({timeout // 60}min), max retries: {MAX_RETRIES}[/dim]"
        )

        while (pending or needs_retry) and time.monotonic() < deadline:
            # Retry any queued failures — refresh auth + backoff first
            for label in list(needs_retry):
                # Check deadline before starting a retry
                if time.monotonic() >= deadline:
                    break

                backoff = RATE_LIMIT_BACKOFF[min(retries[label] - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                # Cap backoff to remaining time so we don't sleep past deadline
                remaining = deadline - time.monotonic()
                actual_backoff = min(backoff, max(remaining - 5, 0))
                if actual_backoff <= 0:
                    break

                get_console().print(
                    f"[blue]⏳[/blue] Retrying {label}"
                    f" ({retries[label]}/{MAX_RETRIES})"
                    f" — backoff {actual_backoff:.0f}s + auth refresh..."
                )
                await asyncio.sleep(actual_backoff)
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
                        pending[label] = status.task_id
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

            # Wait for each pending artefact using upstream wait_for_completion.
            # Cap the wait per item so no single artefact monopolises the timeout
            # and leaves no time for retries of failed items.
            for label in list(pending):
                task_id = pending[label]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break

                # Reserve at least 35s per other pending item (30s backoff + 5s buffer)
                # so they all get a chance to be retried if needed.
                other_pending = len(pending) - 1 + len(needs_retry)
                reserved = other_pending * 35
                max_wait = max(remaining - reserved, 30)

                try:
                    final_status = await _wait_for_artefact(
                        client, notebook_id, task_id, max_wait, label
                    )
                except Exception as e:
                    get_console().print(
                        f"[yellow]⚠[/yellow] Poll error for {label}: {e} — refreshing auth"
                    )
                    await client.refresh_auth()
                    continue

                if final_status.is_complete:
                    get_console().print(f"[green]✓[/green] {label.capitalize()} ready")
                    completed.add(label)
                    pending.pop(label)
                elif final_status.is_failed:
                    pending.pop(label)
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
                    # Still in progress — stays in pending
                    elapsed = int(time.monotonic() - start_time)
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
        completed=completed | already_completed,
        failed=permanently_failed | timed_out,
        quota_exhausted=quota_exhausted,
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

# Download specs using upstream public list/download methods
_DOWNLOAD_SPECS = [
    ("audio", "list_audio", "download_audio", "audio_overview.mp3"),
    ("video", "list_video", "download_video", "video_overview.mp4"),
    ("slides", "list_slide_decks", "download_slide_deck", "slides.pdf"),
    ("infographic", "list_infographics", "download_infographic", "infographic.png"),
]


async def download_artefacts(notebook_id: str, output_dir: Path) -> None:
    """Download all available artefacts from a notebook."""
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await NotebookLMClient.from_storage() as client:
        for label, list_method, dl_method, filename in _DOWNLOAD_SPECS:
            items = await _with_reauth(
                client,
                lambda lm=list_method: getattr(client.artifacts, lm)(notebook_id),
                f"list {label}",
            )
            if not items:
                continue
            # Skip failed artefacts — use upstream .is_completed property
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
