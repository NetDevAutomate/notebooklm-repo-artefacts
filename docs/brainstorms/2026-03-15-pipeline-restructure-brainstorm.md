# Brainstorm: Pipeline Restructure — Event-Driven Stages with Validation Gates

**Date:** 2026-03-15
**Status:** Draft
**Related:** [UPSTREAM_DEVIATION_REPORT.md](../../UPSTREAM_DEVIATION_REPORT.md)

## Context

The current `pipeline()` function in `cli.py` is a ~250-line monolithic function that orchestrates 8 steps sequentially. During a single session on 2026-03-15, it caused:

1. **Repo deletion** — `--store` received an absolute path, `Path(base) / "/absolute"` discarded the base, `shutil.rmtree` deleted the user's actual repo
2. **Artefact deletion** — `_delete_existing_by_type` used wrong integer type codes (VIDEO/SLIDES swapped), deleting slides when asked to delete video
3. **Source ingestion race** — generation started before NotebookLM finished ingesting the PDF
4. **Stale artefact serving** — pipeline skipped regeneration when source was replaced because completed artefacts existed

All four bugs stem from the same root cause: **no validation between stages**.

## What We're Building

A Python-native, event-driven pipeline with:

- **Discrete stages** — collect, upload, ingest-wait, generate (per-artefact), download, publish, verify
- **Pre/post validation gates** — each stage has preconditions checked before execution and postconditions verified after
- **Event-driven transitions** — stages emit events (`upload:complete`, `generate:failed`, `ingest:ready`) that drive the next action
- **Idempotent by default** — running twice never destroys work; only creates what's missing
- **Resumable state** — JSON state file persisted after each stage transition, `--resume` picks up where it left off
- **Remediation logic** — on failure, stages can attempt automatic fixes before aborting

## Why Event-Driven

The user's key insight: upload and ingestion are async operations that need **polling with event-based transitions**, not sequential step execution. When source ingestion completes (event), we transition to generation. When generation fails (event), we retry or remediate. This maps naturally to an event-driven pattern.

```
collect:complete → upload:start
upload:complete → ingest:polling
ingest:ready → generate:start(audio)
                generate:start(video)
                generate:start(slides)
                generate:start(infographic)
generate:complete(audio) → (wait for others)
generate:failed(slides) → generate:retry(slides)
generate:all_complete → download:start
download:complete → publish:start
publish:complete → verify:start
verify:pass → cleanup (optional)
```

## Key Decisions

### 1. Python-native, not external orchestrator

Stages are classes with `pre_check()`, `execute()`, `post_check()`, `remediate()` methods. Keeps everything testable with pytest, no external tools needed.

### 2. Event-driven with polling for async operations

Upload + ingestion and generation are async on NotebookLM's side. The pipeline polls with events rather than blocking synchronously. This allows parallel artefact generation with independent failure/retry per type.

### 3. JSON state file for persistence

One `.pipeline-state.json` per run in the artefacts output dir. Contains notebook ID, stage statuses, completed/pending artefacts. Git-ignorable. The historical audit trail is already in git log + manifest.json.

### 4. Idempotency as the default

- **Collect**: always regenerates (fast, no side effects)
- **Upload**: replaces source if content hash differs, skips if same
- **Generate**: checks existing artefacts via public `artifacts.list()` API. Only generates missing types. Never deletes completed artefacts unless `--force-regen` flag.
- **Download**: overwrites local files (idempotent)
- **Publish**: upserts to store (idempotent)

### 5. Per-artefact generation as sub-events

Each artefact type (audio, video, slides, infographic) is an independent generation event. Failure of one doesn't block others. The pipeline tracks `completed`, `pending`, `failed`, `quota_exhausted` per type.

## Proposed Stage Design

```python
@dataclass
class StageResult:
    status: Literal["pass", "fail", "skip", "retry"]
    message: str = ""
    data: dict = field(default_factory=dict)

class Stage(Protocol):
    name: str
    def pre_check(self, ctx: PipelineContext) -> StageResult: ...
    def execute(self, ctx: PipelineContext) -> StageResult: ...
    def post_check(self, ctx: PipelineContext) -> StageResult: ...
    def remediate(self, ctx: PipelineContext) -> StageResult: ...

class PipelineContext:
    repo_path: Path
    store_slug: str | None
    notebook_id: str | None
    state: PipelineState          # persisted to JSON
    artefacts_dir: Path
    config: dict                  # from config.toml

class Pipeline:
    stages: list[Stage]
    ctx: PipelineContext

    def on(self, event: str, handler: Callable): ...
    def emit(self, event: str, data: dict): ...

    def run(self):
        for stage in self.stages:
            pre = stage.pre_check(self.ctx)
            if pre.status == "skip":
                self.emit(f"{stage.name}:skipped", {})
                continue
            if pre.status == "fail":
                fix = stage.remediate(self.ctx)
                if fix.status != "pass":
                    self.emit(f"{stage.name}:failed", {})
                    break

            result = stage.execute(self.ctx)
            self.ctx.state.update(stage.name, result)
            self.ctx.state.save()

            post = stage.post_check(self.ctx)
            if post.status == "fail":
                self.emit(f"{stage.name}:failed", post.data)
                # retry logic here
            else:
                self.emit(f"{stage.name}:complete", result.data)
```

## Stages Breakdown

| Stage | Pre-check | Execute | Post-check | Remediate |
|-------|-----------|---------|------------|-----------|
| **collect** | Repo path exists, is git repo | Collect files, render PDF | PDF exists, size > 0 | — |
| **upload** | PDF exists | Upload to NotebookLM | Source appears in notebook | Re-upload |
| **ingest** | Source uploaded | Poll `wait_until_ready()` | Source status = READY | Delete + re-upload |
| **generate** | Source ready, check existing artefacts | Generate missing types | Each type completed or quota-exhausted | Retry with backoff |
| **download** | Artefacts completed | Download to local dir | Files exist, size > 0 | Re-download |
| **publish** | Store slug valid, local files exist | Copy to store, push | Git push succeeded | Pull --rebase, retry |
| **verify** | Store pushed | HTTP check URLs | All URLs return 200 | Wait + retry |
| **readme** | Artefact URLs known | Update README links | Links in README match store | — |
| **cleanup** | All stages pass | Delete notebook (optional) | — | — |

## Resolved Questions

1. **Timeout strategy** — Per-stage configurable timeouts with sensible defaults: collect=60s, upload=180s, ingest=180s, generate=900s per type, download=120s, publish=60s, verify=120s.

2. **Generation ordering** — Sequential with a 30-second gap between each artefact type to avoid hitting NotebookLM API rate limits. One failure doesn't block subsequent types.

3. **Content hashing** — SHA256 hash of collected PDF stored in pipeline state. If hash matches previous run, skip upload+generate entirely. Most precise staleness check.

## Out of Scope

- External orchestrators (Taskfile, invoke, act) — Python-native chosen
- Database persistence — JSON state file chosen
- Cross-machine pipeline sync — not needed, git + manifest provides this
