"""NotebookLM integration for uploading repo content and generating artefacts."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

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

logger = logging.getLogger(__name__)

REAUTH_BACKOFF = [2, 10, 30]  # seconds between re-auth retries
RATE_LIMIT_BACKOFF = [5, 15, 30, 60, 120]  # exponential backoff for retries

CONCURRENCY_LIMIT = 2  # max concurrent generation requests
POLL_WINDOW = 60.0  # seconds per polling cycle before checking for retries

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


MAX_RETRIES = 5


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
            logger.warning(
                "[%s] RateLimitError: %s — backoff %ds, attempt %d/%d",
                label,
                e,
                bk,
                attempt,
                len(backoffs),
            )
            get_console().print(
                f"[yellow]⚠[/yellow] {label} rate limited — "
                f"backoff {bk}s then re-auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(bk)
            await client.refresh_auth()
            logger.info("[%s] Auth refreshed after rate limit", label)
            get_console().print("[green]✓[/green] Auth refreshed after rate limit")
        except AuthError as e:
            last_exc = e
            logger.warning(
                "[%s] AuthError: %s — refreshing, attempt %d/%d", label, e, attempt, len(backoffs)
            )
            get_console().print(
                f"[yellow]⚠[/yellow] {label} auth/CSRF expired — "
                f"refreshing (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()
            logger.info("[%s] Auth refreshed after AuthError", label)
            get_console().print("[green]✓[/green] Auth refreshed")
        except RPCError as e:
            last_exc = e
            logger.warning(
                "[%s] RPCError: %s — refreshing auth, attempt %d/%d",
                label,
                e,
                attempt,
                len(backoffs),
            )
            get_console().print(
                f"[yellow]⚠[/yellow] {label} RPC error: {e} — "
                f"refreshing auth (attempt {attempt}/{len(backoffs)})"
            )
            await asyncio.sleep(wait)
            await client.refresh_auth()

    # Final attempt after all backoffs exhausted
    logger.info("[%s] Final attempt after %d retries", label, len(backoffs))
    try:
        return await fn()
    except RPCError as exc:
        logger.error("[%s] FAILED after all retries: %s", label, exc)
        raise (last_exc or RPCError(f"{label} failed after re-auth retries")) from exc  # type: ignore[call-arg]


def _is_quota_error(error_msg: str | None, error_code: str | None = None) -> bool:
    """Check if an error indicates daily quota exhaustion.

    Checks both the error message text (pattern matching) and the error_code
    field. In v0.3.4, GenerationStatus.error can be None even for quota
    failures, but error_code='USER_DISPLAYABLE_ERROR' is reliably set.
    """
    if error_code and error_code.upper() == "USER_DISPLAYABLE_ERROR":
        return True
    if error_msg:
        lower = error_msg.lower()
        return any(p in lower for p in QUOTA_ERROR_PATTERNS)
    return False


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
    logger.info(
        "upload_repo: content=%s repo=%s notebook_id=%s", content_path, repo_name, notebook_id
    )
    async with await NotebookLMClient.from_storage() as client:
        source_replaced = False
        if notebook_id:
            # Resume or explicit override — use existing notebook as-is
            nb_id = notebook_id
            nb_title = repo_name
            logger.info("Using existing notebook: %s", nb_id)
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

        # Wait for source processing before generation can succeed
        get_console().print("  [blue]⏳[/blue] Waiting for source processing...")
        max_wait = 120  # seconds
        poll_interval = 5
        elapsed = 0
        while elapsed < max_wait:
            sources = await _with_reauth(
                client, lambda: client.sources.list(nb_id), "poll source status"
            )
            if not sources:
                logger.warning("No sources found after upload — retrying poll")
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue

            all_ready = all(s.is_ready for s in sources)
            any_error = any(s.is_error for s in sources)
            processing = [s.title for s in sources if s.is_processing]

            if any_error:
                error_sources = [s.title for s in sources if s.is_error]
                logger.error("Source processing failed: %s", error_sources)
                get_console().print(f"  [red]✗[/red] Source processing failed: {error_sources}")
                break
            if all_ready:
                logger.info("All %d source(s) ready", len(sources))
                get_console().print(
                    f"  [green]✓[/green] Source processing complete ({len(sources)} source(s) ready)"
                )
                break

            logger.debug("Sources still processing: %s (%ds)", processing, elapsed)
            get_console().print(f"  [dim]  … processing ({elapsed}s)[/dim]")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        else:
            logger.warning("Source processing timed out after %ds — proceeding anyway", max_wait)
            get_console().print(
                f"  [yellow]⚠[/yellow] Source processing timed out after {max_wait}s — proceeding"
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
) -> Artifact:
    """Poll a single artefact generation until complete, failed, or timeout.

    Unlike upstream wait_for_completion(), this uses manual polling with
    short intervals so concurrent gather() can check all pending artefacts
    regularly. Each poll yields control back to the event loop.

    Note: client.artifacts.get() returns an Artifact (has is_completed),
    not a GenerationStatus (has is_complete).
    """
    deadline = time.monotonic() + timeout
    interval = 2.0
    while time.monotonic() < deadline:
        status = await _with_reauth(
            client,
            lambda: client.artifacts.get(notebook_id, task_id),
            f"poll {label}",
        )
        if status.is_completed or status.is_failed:
            return status
        # Still generating — sleep briefly then let gather() check others
        sleep_time = min(interval, deadline - time.monotonic())
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        interval = min(interval * 1.5, 10.0)  # exponential backoff, max 10s

    # Timeout — return current status (likely still in_progress)
    return await _with_reauth(
        client,
        lambda: client.artifacts.get(notebook_id, task_id),
        f"poll {label} (final)",
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

    Submits up to CONCURRENCY_LIMIT generation requests concurrently,
    then polls with short windows (POLL_WINDOW) so failed items can be
    retried promptly without waiting for slow successes to finish.
    Retries are also submitted concurrently after a shared backoff.

    Args:
        force_regen: If True, delete ALL existing artefacts of each type before
            regenerating. If False (default), only delete FAILED artefacts and
            skip types that are already completed.
    """
    logger.info(
        "generate_artefacts: notebook=%s artefacts=%s timeout=%ds force_regen=%s",
        notebook_id,
        artefacts,
        timeout,
        force_regen,
    )
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

        # Submit initial generation requests concurrently (max CONCURRENCY_LIMIT at a time)
        sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

        async def _submit_one(artefact: str) -> None:
            async with sem:
                logger.info("[submit] %s: requesting generation", artefact)
                get_console().print(f"[blue]⏳[/blue] Requesting {artefact}...")
                try:
                    await _delete_existing_by_type(
                        client, notebook_id, artefact, failed_only=not force_regen
                    )
                    status = await _request_artefact(client, notebook_id, artefact)
                    logger.debug(
                        "[submit] %s: response — task_id=%s status=%s error=%r error_code=%r",
                        artefact,
                        status.task_id,
                        status.status,
                        status.error,
                        status.error_code,
                    )
                    if status.is_failed or not status.task_id:
                        err = status.error or "no artifact_id returned"
                        err_detail = (
                            f"error={status.error!r}, error_code={status.error_code!r},"
                            f" task_id={status.task_id!r}, status={status.status!r},"
                            f" metadata={status.metadata!r}"
                        )
                        logger.warning("[submit] %s: immediate failure — %s", artefact, err_detail)
                        if _is_quota_error(err, status.error_code):
                            get_console().print(
                                f"[yellow]⚠[/yellow] {artefact} rejected ({err})"
                                " — refreshing auth to confirm..."
                            )
                            logger.warning(
                                "[submit] %s: quota suspected — refreshing auth to confirm",
                                artefact,
                            )
                            await client.refresh_auth()
                            await asyncio.sleep(5)
                            status = await _request_artefact(client, notebook_id, artefact)
                            if status.is_failed or not status.task_id:
                                logger.error("[submit] %s: QUOTA EXHAUSTED confirmed", artefact)
                                quota_exhausted.add(artefact)
                                get_console().print(
                                    f"[red]✗[/red] {artefact}: daily quota exhausted"
                                    " (NotebookLM caps infographics/slides"
                                    " separately). Retry after 24h reset."
                                )
                                return
                            logger.info(
                                "[submit] %s: quota false alarm — task_id=%s",
                                artefact,
                                status.task_id,
                            )
                            pending[artefact] = status.task_id
                            return
                        retries[artefact] += 1
                        logger.warning(
                            "[submit] %s: transient failure, retry %d/%d — %s",
                            artefact,
                            retries[artefact],
                            MAX_RETRIES,
                            err,
                        )
                        get_console().print(
                            f"[yellow]⚠[/yellow] {artefact} failed immediately"
                            f" ({err})"
                            f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                        )
                        get_console().print(f"  [dim]Detail: {err_detail}[/dim]")
                        await client.refresh_auth()
                    else:
                        logger.info("[submit] %s: accepted — task_id=%s", artefact, status.task_id)
                        pending[artefact] = status.task_id
                except Exception as e:
                    retries[artefact] += 1
                    logger.exception(
                        "[submit] %s: exception on request, retry %d/%d",
                        artefact,
                        retries[artefact],
                        MAX_RETRIES,
                    )
                    get_console().print(
                        f"[yellow]⚠[/yellow] Failed to request {artefact}: {e}"
                        f" — will retry ({retries[artefact]}/{MAX_RETRIES})"
                    )

        await asyncio.gather(*[_submit_one(a) for a in to_generate])

        # Queue initial failures for retry (excluding quota-exhausted)
        needs_retry = [
            a
            for a in to_generate
            if a not in pending and a not in quota_exhausted and retries[a] <= MAX_RETRIES
        ]

        start_time = time.monotonic()
        deadline = start_time + timeout

        get_console().print(
            f"[dim]Timeout: {timeout}s ({timeout // 60}min), max retries: {MAX_RETRIES},"
            f" concurrency: {CONCURRENCY_LIMIT}[/dim]"
        )

        async def _poll_one(
            label: str, task_id: str, poll_timeout: float
        ) -> tuple[str, Artifact | None, Exception | None]:
            try:
                status = await _wait_for_artefact(
                    client, notebook_id, task_id, poll_timeout, label
                )
                return label, status, None
            except Exception as e:
                return label, None, e

        while (pending or needs_retry) and time.monotonic() < deadline:
            elapsed = int(time.monotonic() - start_time)
            remaining_s = int(deadline - time.monotonic())
            logger.debug(
                "[poll] loop: pending=%s needs_retry=%s elapsed=%ds remaining=%ds",
                list(pending.keys()),
                needs_retry,
                elapsed,
                remaining_s,
            )
            # Poll pending artefacts with a SHORT window so we can detect
            # failures quickly and start retries without waiting for slow
            # successes to finish.
            if pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                poll_window = min(POLL_WINDOW, remaining)

                results = await asyncio.gather(
                    *[_poll_one(label, tid, poll_window) for label, tid in pending.items()]
                )

                for label, final_status, exc in results:
                    if exc is not None or final_status is None:
                        logger.warning("[poll] %s: error — %s", label, exc)
                        get_console().print(
                            f"[yellow]⚠[/yellow] Poll error for {label}: {exc} — refreshing auth"
                        )
                        await client.refresh_auth()
                        continue

                    logger.debug(
                        "[poll] %s: is_completed=%s is_failed=%s status=%s error=%r",
                        label,
                        final_status.is_completed,
                        final_status.is_failed,
                        getattr(final_status, "status", "?"),
                        getattr(final_status, "error", None),
                    )

                    if final_status.is_completed:
                        logger.info("[poll] %s: COMPLETED", label)
                        get_console().print(f"[green]✓[/green] {label.capitalize()} ready")
                        completed.add(label)
                        pending.pop(label)
                    elif final_status.is_failed:
                        pending.pop(label)
                        retries[label] += 1
                        logger.warning(
                            "[poll] %s: FAILED — error=%r, retry %d/%d",
                            label,
                            getattr(final_status, "error", None),
                            retries[label],
                            MAX_RETRIES,
                        )
                        if retries[label] <= MAX_RETRIES:
                            await _delete_existing_by_type(
                                client, notebook_id, label, failed_only=True
                            )
                            get_console().print(
                                f"[yellow]⚠[/yellow] {label} failed"
                                f" — queued retry ({retries[label]}/{MAX_RETRIES})"
                            )
                            if label not in needs_retry:
                                needs_retry.append(label)
                        else:
                            await _delete_existing_by_type(
                                client, notebook_id, label, failed_only=True
                            )
                            logger.error(
                                "[poll] %s: PERMANENTLY FAILED after %d retries",
                                label,
                                MAX_RETRIES,
                            )
                            get_console().print(
                                f"[red]✗[/red] {label} failed after {MAX_RETRIES} retries"
                            )
                            permanently_failed.add(label)
                    else:
                        # Still in progress — stays in pending for next poll cycle
                        elapsed = int(time.monotonic() - start_time)
                        logger.debug("[poll] %s: still generating (%ds)", label, elapsed)
                        get_console().print(
                            f"[dim]  … {label} still generating ({elapsed}s)[/dim]"
                        )

            # Handle retries concurrently — single shared backoff, then batch submit
            if needs_retry and time.monotonic() < deadline:
                max_backoff = max(
                    RATE_LIMIT_BACKOFF[min(retries[label] - 1, len(RATE_LIMIT_BACKOFF) - 1)]
                    for label in needs_retry
                )
                remaining = deadline - time.monotonic()
                actual_backoff = min(max_backoff, max(remaining - 10, 0))
                if actual_backoff <= 0:
                    break

                get_console().print(
                    f"[blue]⏳[/blue] Retrying {len(needs_retry)} artefact(s)"
                    f" — backoff {actual_backoff:.0f}s + auth refresh..."
                )
                await asyncio.sleep(actual_backoff)
                await client.refresh_auth()
                get_console().print("[green]✓[/green] Auth refreshed")

                retry_batch = list(needs_retry)

                async def _retry_one(label: str) -> None:
                    async with sem:
                        try:
                            await _delete_existing_by_type(
                                client, notebook_id, label, failed_only=True
                            )
                            status = await _request_artefact(client, notebook_id, label)
                            if status.is_failed or not status.task_id:
                                retries[label] += 1
                                if retries[label] > MAX_RETRIES:
                                    await _delete_existing_by_type(
                                        client, notebook_id, label, failed_only=True
                                    )
                                    get_console().print(
                                        f"[red]✗[/red] {label} failed after"
                                        f" {MAX_RETRIES} retries: {status.error}"
                                    )
                                    permanently_failed.add(label)
                            else:
                                pending[label] = status.task_id
                        except Exception as e:
                            retries[label] += 1
                            if retries[label] > MAX_RETRIES:
                                await _delete_existing_by_type(
                                    client, notebook_id, label, failed_only=True
                                )
                                get_console().print(
                                    f"[red]✗[/red] {label} failed after {MAX_RETRIES} retries: {e}"
                                )
                                permanently_failed.add(label)

                await asyncio.gather(*[_retry_one(label) for label in retry_batch])

                # Update needs_retry: keep only items still eligible
                needs_retry = [
                    a
                    for a in needs_retry
                    if a not in pending
                    and a not in permanently_failed
                    and retries.get(a, 0) <= MAX_RETRIES
                ]

        timed_out: set[str] = set()
        for label in list(pending) + needs_retry:
            logger.error("[timeout] %s: TIMED OUT after %ds", label, timeout)
            get_console().print(f"[red]✗[/red] {label.capitalize()} timed out")
            timed_out.add(label)
            # Clean up any failed artefacts from timed-out items
            await _delete_existing_by_type(client, notebook_id, label, failed_only=True)

        if quota_exhausted:
            logger.warning("Quota-exhausted artefacts: %s", sorted(quota_exhausted))
            get_console().print(
                f"\n[yellow]i[/yellow] Quota-limited artefacts: "
                f"[bold]{', '.join(sorted(quota_exhausted))}[/bold]"
                "\n  NotebookLM enforces separate daily caps for"
                " infographics (~20-25/day Pro) and slides."
                "\n  These reset 24h from first daily use (UTC)."
                "\n  Retry tomorrow: repo-artefacts generate"
                f" {''.join(f' --{a}' for a in sorted(quota_exhausted))}"
            )

    result = GenerateResult(
        completed=completed | already_completed,
        failed=permanently_failed | timed_out,
        quota_exhausted=quota_exhausted,
    )
    logger.info(
        "generate_artefacts result: completed=%s failed=%s quota_exhausted=%s",
        sorted(result.completed),
        sorted(result.failed),
        sorted(result.quota_exhausted),
    )
    get_console().print("[bold green]Done.[/bold green]")
    return result


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
    logger.info("download_artefacts: notebook=%s output=%s", notebook_id, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    async with await NotebookLMClient.from_storage() as client:
        for label, list_method, dl_method, filename in _DOWNLOAD_SPECS:
            items = await _with_reauth(
                client,
                lambda lm=list_method: getattr(client.artifacts, lm)(notebook_id),
                f"list {label}",
            )
            logger.debug("[download] %s: found %d items", label, len(items) if items else 0)
            if not items:
                continue
            # Skip failed artefacts — use upstream .is_completed property
            ready = [i for i in items if i.is_completed]
            if not ready:
                logger.warning(
                    "[download] %s: %d items exist but none completed", label, len(items)
                )
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
