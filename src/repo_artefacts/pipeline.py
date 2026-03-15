"""Event-driven pipeline with discrete stages, validation gates, and state persistence.

Each stage has pre_check → execute → post_check methods. The runner drives
transitions based on results (pass/fail/skip/retry) and persists state to
a JSON file after each stage for resumability.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from notebooklm import NotebookLMClient

from repo_artefacts.collector import collect_repo_content, render_to_pdf
from repo_artefacts.console import get_console
from repo_artefacts.notebooklm import (
    ARTEFACT_CONFIG,
    NAME_TO_KIND,
    _request_artefact,
    _with_reauth,
    download_artefacts,
    get_completed_artefacts,
    upload_repo,
)
from repo_artefacts.store import (
    StoreError,
    _validate_store_slug,
    clone_or_pull_store,
    commit_and_push_store,
    publish_to_store,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    RETRY = "retry"


@dataclass
class StageResult:
    status: Status
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline state — persisted to JSON after each stage
# ---------------------------------------------------------------------------

STATE_FILENAME = ".pipeline-state.json"


@dataclass
class PipelineState:
    """Persisted pipeline state."""

    repo_name: str = ""
    notebook_id: str = ""
    content_hash: str = ""
    source_replaced: bool = False
    stages: dict[str, dict[str, Any]] = field(default_factory=dict)
    artefacts: dict[str, str] = field(default_factory=dict)  # name → status
    started_at: str = ""
    updated_at: str = ""

    def save(self, path: Path) -> None:
        self.updated_at = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(self.__dict__, indent=2, default=str) + "\n")

    @classmethod
    def load(cls, path: Path) -> PipelineState:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        state = cls()
        for k, v in data.items():
            if hasattr(state, k):
                setattr(state, k, v)
        return state

    def stage_status(self, name: str) -> str:
        return self.stages.get(name, {}).get("status", "")

    def set_stage(self, name: str, status: str, **extra: Any) -> None:
        self.stages[name] = {
            "status": status,
            "at": datetime.now(UTC).isoformat(),
            **extra,
        }


# ---------------------------------------------------------------------------
# Pipeline context — shared across all stages
# ---------------------------------------------------------------------------


@dataclass
class PipelineContext:
    repo_path: Path
    store_slug: str | None = None
    output_dir: Path = field(default_factory=lambda: Path("docs/artefacts"))
    keep_notebook: bool = False
    force_regen: bool = False
    dry_run: bool = False
    timeout: int = 900
    state: PipelineState = field(default_factory=PipelineState)
    state_path: Path = field(default_factory=lambda: Path(STATE_FILENAME))

    # Set during execution
    pdf_path: Path | None = None
    md_path: Path | None = None

    def save_state(self) -> None:
        self.state.save(self.state_path)


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class CollectStage:
    """Collect repo content and render to PDF."""

    name = "collect"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.repo_path.exists():
            return StageResult(Status.FAIL, f"Repo path does not exist: {ctx.repo_path}")
        if not (ctx.repo_path / ".git").is_dir():
            return StageResult(Status.FAIL, f"Not a git repo: {ctx.repo_path}")
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        ctx.output_dir.mkdir(parents=True, exist_ok=True)
        md_path = ctx.output_dir / f"{ctx.state.repo_name}_content.md"
        collect_repo_content(ctx.repo_path, md_path)
        pdf_path = render_to_pdf(md_path)
        ctx.pdf_path = pdf_path
        ctx.md_path = md_path

        content_hash = _hash_file(pdf_path)
        ctx.state.content_hash = content_hash
        return StageResult(
            Status.PASS,
            f"Collected {pdf_path.stat().st_size / 1024:.1f} KB",
            {"pdf_path": str(pdf_path), "content_hash": content_hash},
        )

    def post_check(self, ctx: PipelineContext) -> StageResult:
        if ctx.pdf_path and ctx.pdf_path.exists() and ctx.pdf_path.stat().st_size > 0:
            return StageResult(Status.PASS)
        return StageResult(Status.FAIL, "PDF not created or empty")


class UploadStage:
    """Upload PDF to NotebookLM."""

    name = "upload"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.pdf_path or not ctx.pdf_path.exists():
            return StageResult(Status.FAIL, "No PDF to upload")

        # Content hash skip: if the PDF we just collected has the same hash
        # as what was previously uploaded (stored in state), skip the upload.
        # This only triggers on --resume where state carries the previous hash.
        prev_hash = ctx.state.stages.get("upload", {}).get("content_hash", "")
        if (
            not ctx.force_regen
            and prev_hash
            and ctx.state.notebook_id
            and ctx.state.content_hash == prev_hash
        ):
            get_console().print(
                f"  [dim]Content unchanged (hash {prev_hash[:12]}…) — skipping upload[/dim]"
            )
            return StageResult(Status.SKIP, "Content hash unchanged")

        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        assert ctx.pdf_path is not None
        result = asyncio.run(
            upload_repo(ctx.pdf_path, ctx.state.repo_name, ctx.state.notebook_id or None)
        )
        ctx.state.notebook_id = result["id"]
        ctx.state.source_replaced = bool(result.get("source_replaced"))

        # Clean up temp files
        if ctx.md_path:
            ctx.md_path.unlink(missing_ok=True)
        ctx.pdf_path.unlink(missing_ok=True)

        return StageResult(
            Status.PASS,
            f"Notebook: {ctx.state.notebook_id}",
            {
                "notebook_id": ctx.state.notebook_id,
                "source_replaced": ctx.state.source_replaced,
                "content_hash": ctx.state.content_hash,
            },
        )

    def post_check(self, ctx: PipelineContext) -> StageResult:
        if ctx.state.notebook_id:
            return StageResult(Status.PASS)
        return StageResult(Status.FAIL, "No notebook ID after upload")


class GenerateStage:
    """Generate artefacts sequentially with 30s gap. Only generates missing types."""

    name = "generate"
    gap_seconds = 30

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.state.notebook_id:
            return StageResult(Status.FAIL, "No notebook ID")
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        nb_id = ctx.state.notebook_id

        # Check what's already completed
        already_done: set[str] = set()
        if not ctx.force_regen and not ctx.state.source_replaced:
            already_done = asyncio.run(get_completed_artefacts(nb_id))
            if already_done:
                get_console().print(
                    f"  Already completed: [green]{', '.join(sorted(already_done))}[/green]"
                )

        if ctx.state.source_replaced:
            get_console().print("  [yellow]Source replaced — regenerating all artefacts[/yellow]")

        target = [a for a in ARTEFACT_CONFIG if a not in already_done]
        if not target:
            get_console().print("  [green]All artefacts already generated[/green]")
            for name in ARTEFACT_CONFIG:
                ctx.state.artefacts[name] = "completed"
            return StageResult(Status.PASS, "All artefacts already completed")

        # Generate each type sequentially with gap
        completed: list[str] = []
        failed: list[str] = []

        async def _generate_all() -> None:
            async with await NotebookLMClient.from_storage() as client:
                for i, artefact in enumerate(target):
                    if i > 0:
                        get_console().print(
                            f"  [dim]Waiting {self.gap_seconds}s before next generation...[/dim]"
                        )
                        await asyncio.sleep(self.gap_seconds)

                    get_console().print(f"  [blue]Generating {artefact}...[/blue]")

                    # Only delete FAILED artefacts, never completed
                    artifacts = await _with_reauth(
                        client, lambda: client.artifacts.list(nb_id), f"list {artefact}"
                    )
                    target_kind = NAME_TO_KIND[artefact]
                    for art in artifacts:
                        if art.kind == target_kind and art.is_failed:
                            get_console().print(
                                f"    [dim]Deleting failed {artefact} ({art.id[:12]}...)[/dim]"
                            )
                            await _with_reauth(
                                client,
                                lambda aid=art.id: client.artifacts.delete(nb_id, aid),
                                f"delete failed {artefact}",
                            )

                    # Request generation
                    try:
                        gen_status = await _with_reauth(
                            client,
                            lambda a=artefact: _request_artefact(client, nb_id, a),
                            f"generate {artefact}",
                        )
                        if gen_status.is_failed or not gen_status.task_id:
                            get_console().print(
                                f"  [red]Failed to start {artefact}: {gen_status.error}[/red]"
                            )
                            failed.append(artefact)
                            ctx.state.artefacts[artefact] = "failed"
                            continue
                    except Exception as e:
                        get_console().print(f"  [red]Error generating {artefact}: {e}[/red]")
                        failed.append(artefact)
                        ctx.state.artefacts[artefact] = "failed"
                        continue

                    # Wait for completion using upstream exponential-backoff poller
                    start = time.monotonic()
                    try:
                        final_status = await _with_reauth(
                            client,
                            lambda tid=gen_status.task_id: client.artifacts.wait_for_completion(
                                nb_id,
                                tid,
                                initial_interval=2.0,
                                max_interval=10.0,
                                timeout=float(ctx.timeout),
                            ),
                            f"wait {artefact}",
                        )
                        elapsed = int(time.monotonic() - start)
                        if final_status.is_complete:
                            get_console().print(
                                f"  [green]✓ {artefact} ready ({elapsed}s)[/green]"
                            )
                            completed.append(artefact)
                            ctx.state.artefacts[artefact] = "completed"
                        elif final_status.is_failed:
                            get_console().print(
                                f"  [red]✗ {artefact} failed ({elapsed}s): "
                                f"{final_status.error}[/red]"
                            )
                            failed.append(artefact)
                            ctx.state.artefacts[artefact] = "failed"
                        else:
                            # Unexpected terminal state — treat as failure
                            get_console().print(
                                f"  [red]✗ {artefact} ended with status "
                                f"'{final_status.status}' ({elapsed}s)[/red]"
                            )
                            failed.append(artefact)
                            ctx.state.artefacts[artefact] = "failed"
                    except TimeoutError:
                        elapsed = int(time.monotonic() - start)
                        get_console().print(f"  [red]✗ {artefact} timed out ({elapsed}s)[/red]")
                        failed.append(artefact)
                        ctx.state.artefacts[artefact] = "timed_out"

        asyncio.run(_generate_all())

        # Mark already-done ones
        for name in already_done:
            ctx.state.artefacts[name] = "completed"

        if failed:
            return StageResult(
                Status.FAIL,
                f"Failed: {', '.join(failed)}. Completed: {', '.join(completed)}",
                {"completed": completed, "failed": failed},
            )
        return StageResult(
            Status.PASS,
            f"Generated: {', '.join(completed or list(already_done))}",
            {"completed": completed + list(already_done)},
        )

    def post_check(self, ctx: PipelineContext) -> StageResult:
        all_done = all(ctx.state.artefacts.get(name) == "completed" for name in ARTEFACT_CONFIG)
        if all_done:
            return StageResult(Status.PASS)
        missing = [
            name for name in ARTEFACT_CONFIG if ctx.state.artefacts.get(name) != "completed"
        ]
        return StageResult(Status.FAIL, f"Not all artefacts completed: {missing}")


class DownloadStage:
    """Download completed artefacts."""

    name = "download"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.state.notebook_id:
            return StageResult(Status.FAIL, "No notebook ID")
        completed = [n for n in ARTEFACT_CONFIG if ctx.state.artefacts.get(n) == "completed"]
        if not completed:
            return StageResult(Status.FAIL, "No completed artefacts to download")
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        asyncio.run(download_artefacts(ctx.state.notebook_id, ctx.output_dir))
        return StageResult(Status.PASS, f"Downloaded to {ctx.output_dir}")

    def post_check(self, ctx: PipelineContext) -> StageResult:
        from repo_artefacts.publish import check_artefacts

        found = check_artefacts(ctx.output_dir)
        if not found:
            return StageResult(Status.FAIL, "No artefact files found after download")
        for kind, path in found.items():
            get_console().print(f"  [green]✓[/green] {kind}: {path.name}")
        return StageResult(Status.PASS, f"Verified {len(found)} artefact files")


class PublishStage:
    """Publish artefacts to the store."""

    name = "publish"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.store_slug:
            return StageResult(Status.SKIP, "No store configured")
        try:
            _validate_store_slug(ctx.store_slug)
        except StoreError as e:
            return StageResult(Status.FAIL, str(e))
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        assert ctx.store_slug is not None
        store_path = clone_or_pull_store(ctx.store_slug)
        base_url = publish_to_store(store_path, ctx.state.repo_name, ctx.output_dir)
        success = commit_and_push_store(store_path, ctx.state.repo_name)
        if not success:
            return StageResult(Status.FAIL, "Store push failed")
        return StageResult(Status.PASS, f"Published to {base_url}", {"base_url": base_url})

    def post_check(self, ctx: PipelineContext) -> StageResult:
        return StageResult(Status.PASS)


class VerifyStage:
    """Verify artefacts are live on the store."""

    name = "verify"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if not ctx.store_slug:
            return StageResult(Status.SKIP, "No store to verify")
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        from repo_artefacts.publish import check_artefacts, verify_pages

        assert ctx.store_slug is not None
        store_path = clone_or_pull_store(ctx.store_slug)
        cname_file = store_path / "CNAME"
        if cname_file.exists():
            domain = cname_file.read_text().strip()
            base_url = f"https://{domain}/{ctx.state.repo_name}/artefacts/"
        else:
            base_url = (
                f"https://{ctx.store_slug.split('/')[0].lower()}.github.io/"
                f"{ctx.store_slug.split('/')[1]}/{ctx.state.repo_name}/artefacts/"
            )

        found = check_artefacts(ctx.output_dir)
        artefact_urls = {kind: base_url + path.name for kind, path in found.items()}
        site_ok, verified = verify_pages(base_url, max_wait=120, artefact_urls=artefact_urls)
        if site_ok:
            return StageResult(
                Status.PASS,
                f"Verified {len(verified)} artefacts at {base_url}",
                {"base_url": base_url, "verified": list(verified)},
            )
        return StageResult(Status.FAIL, f"Verification failed for {base_url}")

    def post_check(self, ctx: PipelineContext) -> StageResult:
        return StageResult(Status.PASS)


class ReadmeStage:
    """Update source repo README with artefact links."""

    name = "readme"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        readme = ctx.repo_path / "README.md"
        if not readme.exists():
            return StageResult(Status.SKIP, "No README.md")
        if not ctx.store_slug:
            return StageResult(Status.SKIP, "No store configured")
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        from repo_artefacts.pages import get_github_info, setup_pages
        from repo_artefacts.publish import check_artefacts, git_commit_and_push

        assert ctx.store_slug is not None
        store_path = clone_or_pull_store(ctx.store_slug)

        # Resolve base URL
        cname_file = store_path / "CNAME"
        if cname_file.exists():
            domain = cname_file.read_text().strip()
            base_url = f"https://{domain}/{ctx.state.repo_name}/artefacts/"
        else:
            base_url = ""

        org, repo = get_github_info(ctx.repo_path)
        found = check_artefacts(ctx.output_dir)
        setup_pages(
            ctx.repo_path,
            org,
            repo,
            store_base_url=base_url,
            available_artefacts=set(found),
        )
        git_commit_and_push(
            ctx.repo_path,
            "docs: update artefact links",
            "origin",
            outputs=["README.md"],
        )
        return StageResult(Status.PASS, "README updated")

    def post_check(self, ctx: PipelineContext) -> StageResult:
        return StageResult(Status.PASS)


class CleanupStage:
    """Optionally delete the NotebookLM notebook."""

    name = "cleanup"

    def pre_check(self, ctx: PipelineContext) -> StageResult:
        if ctx.keep_notebook:
            return StageResult(Status.SKIP, "Keeping notebook")
        if not ctx.state.notebook_id:
            return StageResult(Status.SKIP, "No notebook to clean up")
        # Only clean up if all artefacts completed
        all_done = all(ctx.state.artefacts.get(name) == "completed" for name in ARTEFACT_CONFIG)
        if not all_done:
            return StageResult(
                Status.SKIP,
                "Not all artefacts completed — keeping notebook for retry",
            )
        return StageResult(Status.PASS)

    def execute(self, ctx: PipelineContext) -> StageResult:
        from repo_artefacts.notebooklm import delete_notebook

        asyncio.run(delete_notebook(ctx.state.notebook_id))
        return StageResult(Status.PASS, f"Deleted notebook {ctx.state.notebook_id}")

    def post_check(self, ctx: PipelineContext) -> StageResult:
        return StageResult(Status.PASS)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

ALL_STAGES = [
    CollectStage(),
    UploadStage(),
    GenerateStage(),
    DownloadStage(),
    PublishStage(),
    VerifyStage(),
    ReadmeStage(),
    CleanupStage(),
]


def _resolve_repo_name(repo_path: Path) -> str:
    """Derive repo name from git remote or directory name."""
    import subprocess

    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        url = result.stdout.strip()
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name
    return repo_path.resolve().name


def _notify(title: str, message: str) -> None:
    """Send a macOS notification. Silent no-op on other platforms."""
    import platform
    import subprocess as sp

    if platform.system() != "Darwin":
        return
    try:
        sp.run(
            [
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"',
            ],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass  # best-effort


def run_pipeline(
    repo_path: Path,
    *,
    store_slug: str | None = None,
    keep_notebook: bool = False,
    force_regen: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    timeout: int = 900,
) -> bool:
    """Run the full artefact pipeline.

    Returns True if all stages passed.
    """
    repo_path = repo_path.resolve()
    repo_name = _resolve_repo_name(repo_path)
    output_dir = repo_path / "docs" / "artefacts"
    state_path = output_dir / STATE_FILENAME

    # Load or create state
    state = PipelineState.load(state_path) if resume else PipelineState()
    state.repo_name = repo_name
    if not state.started_at:
        state.started_at = datetime.now(UTC).isoformat()

    ctx = PipelineContext(
        repo_path=repo_path,
        store_slug=store_slug,
        output_dir=output_dir,
        keep_notebook=keep_notebook,
        force_regen=force_regen,
        dry_run=dry_run,
        timeout=timeout,
        state=state,
        state_path=state_path,
    )

    console = get_console()
    console.print(f"\n[bold]Pipeline for {repo_name}[/bold]")
    if store_slug:
        console.print(f"  Store: [cyan]{store_slug}[/cyan]")
    if dry_run:
        console.print("  [yellow]DRY RUN — no changes will be made[/yellow]")
    console.print()

    pipeline_start = time.monotonic()
    all_passed = True

    for stage in ALL_STAGES:
        console.rule(f"Stage: {stage.name}")
        stage_start = time.monotonic()

        # Pre-check
        pre = stage.pre_check(ctx)
        if pre.status == Status.SKIP:
            console.print(f"  [dim]Skipped: {pre.message}[/dim]")
            ctx.state.set_stage(stage.name, "skipped", reason=pre.message)
            ctx.save_state()
            continue
        if pre.status == Status.FAIL:
            console.print(f"  [red]Pre-check failed: {pre.message}[/red]")
            ctx.state.set_stage(stage.name, "failed", reason=pre.message)
            ctx.save_state()
            all_passed = False
            break

        # Dry run: show what would happen, don't execute
        if dry_run:
            console.print(f"  [dim]Would execute: {stage.name}[/dim]")
            ctx.state.set_stage(stage.name, "dry_run")
            ctx.save_state()
            continue

        # Execute
        try:
            result = stage.execute(ctx)
        except Exception as e:
            console.print(f"  [red]Error: {e}[/red]")
            ctx.state.set_stage(stage.name, "error", error=str(e))
            ctx.save_state()
            all_passed = False
            break

        if result.status == Status.FAIL:
            console.print(f"  [red]Failed: {result.message}[/red]")
            ctx.state.set_stage(stage.name, "failed", reason=result.message, **result.data)
            ctx.save_state()
            all_passed = False
            break

        # Post-check
        post = stage.post_check(ctx)
        if post.status == Status.FAIL:
            console.print(f"  [red]Post-check failed: {post.message}[/red]")
            ctx.state.set_stage(stage.name, "post_check_failed", reason=post.message)
            ctx.save_state()
            all_passed = False
            break

        # Metrics: track stage duration
        stage_duration = round(time.monotonic() - stage_start, 1)
        console.print(f"  [green]✓ {stage.name}: {result.message}[/green] [dim]({stage_duration}s)[/dim]")
        ctx.state.set_stage(stage.name, "pass", duration_s=stage_duration, **result.data)
        ctx.save_state()

    total_duration = round(time.monotonic() - pipeline_start, 1)

    if all_passed:
        console.print(f"\n[bold green]Pipeline complete![/bold green] [dim]({total_duration}s)[/dim]")
        _notify("repo-artefacts", f"Pipeline complete for {repo_name} ({total_duration}s)")
    else:
        console.print(f"\n[bold red]Pipeline failed.[/bold red] [dim]({total_duration}s)[/dim]")
        console.print(f"State saved to: {state_path}")
        console.print("Resume with: repo-artefacts pipeline --resume ...")
        _notify("repo-artefacts", f"Pipeline FAILED for {repo_name}")

    return all_passed
