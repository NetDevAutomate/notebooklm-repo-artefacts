---
title: "Pipeline Restructure: Monolithic to Stage-Based Architecture"
date: 2026-03-15
category: refactor
tags:
  - pipeline
  - notebooklm-api
  - stage-pattern
  - idempotency
  - validation-gates
  - artefact-generation
severity: high
component: repo_artefacts.pipeline / repo_artefacts.cli / repo_artefacts.notebooklm
status: completed
---

# Pipeline Restructure: From Bug-Causing Monolith to Stage-Based Architecture

## Problem

The `pipeline()` function in `cli.py` was a ~250-line monolithic function orchestrating 8 sequential steps with no validation between them. During a single session on 2026-03-15, this design caused four categories of data loss and corruption:

1. **Repo deletion** -- `--store` received an absolute path (`/Users/.../repo`). Python's `Path(base) / "/absolute"` silently discards the base, so `shutil.rmtree` targeted the user's actual repository.
2. **Artefact deletion** -- Hand-rolled `ArtefactType(IntEnum)` had `VIDEO=8` and `SLIDES=3` swapped vs the upstream `notebooklm-py` library's `VIDEO=3` and `SLIDES=8`. When `_delete_existing_by_type("video")` ran, it matched and deleted the slides artefact.
3. **Source ingestion race** -- Generation was requested immediately after source upload, before NotebookLM finished ingesting the PDF. Artefacts failed because the source wasn't ready.
4. **Stale artefact serving** -- When the pipeline replaced a source PDF, it still treated existing completed artefacts as valid and skipped regeneration.

## Root Cause

All four bugs stemmed from the same design flaw: **no validation between stages**. The monolithic function had no pre-checks, no post-checks, no state persistence, and no safe defaults. Each step assumed the previous step succeeded without verification.

Additional contributing factors:
- **Duplicated upstream types** -- Integer type codes copied by hand instead of imported from the upstream library
- **Private API usage** -- `client.artifacts._list_raw()` returned raw arrays requiring manual parsing of positional indices
- **Destructive defaults** -- `_delete_existing_by_type(failed_only=False)` deleted completed artefacts by default
- **No content hashing** -- No way to detect whether source content actually changed

## Solution

### Stage-Based Pipeline with Validation Gates

Replaced the monolithic function with 8 discrete stages, each with `pre_check()`, `execute()`, and `post_check()` methods:

```
collect -> upload -> generate -> download -> publish -> verify -> readme -> cleanup
```

Each stage returns a `StageResult` with status `PASS`, `FAIL`, `SKIP`, or `RETRY`. The runner halts on any failure and persists state to JSON for resumability.

```python
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
```

### Idempotency (Three Layers)

1. **Content hash skip** -- SHA256 of collected PDF stored in state. On `--resume`, if hash matches previous run, upload is skipped entirely.
2. **Only delete FAILED artefacts** -- Before generation, only artefacts with `is_failed` are cleaned up. Completed artefacts are never touched unless `--force-regen` is explicitly set.
3. **Check completed before generating** -- Queries the API for already-completed artefact types and excludes them from the generation target list.

### Upstream API Adoption

Replaced all private API usage with the public `notebooklm-py` API:

| Before | After |
|--------|-------|
| `client.artifacts._list_raw()` + manual array parsing | `client.artifacts.list()` returning `Artifact` objects |
| Hand-rolled `ArtefactType(IntEnum)` with wrong values | Upstream `ArtifactType` string enum (`"video"`, `"slide_deck"`) |
| Custom 30s polling loop | Upstream `wait_for_completion()` with exponential backoff (2s-10s) |
| `add_file()` without waiting | `add_file(wait=True, wait_timeout=180.0)` blocks until ingestion complete |

### Safety Guarantees

- **Store slug validation** -- `_validate_store_slug()` rejects absolute paths, tilde paths, `..` traversals. Only accepts `org/repo` format.
- **`_safe_rmtree()`** -- Defence-in-depth: refuses to delete directories outside the store cache tree.
- **Sequential generation with 30s gap** -- Avoids NotebookLM API rate limits.
- **macOS notification** on pipeline complete/fail via `osascript`.
- **Stage metrics** -- Duration per stage recorded in state JSON.

## Key Metrics

| Metric | Before | After |
|--------|--------|-------|
| Pipeline functions | 1 monolithic (~250 lines) | 8 stages with validation gates |
| Tests | 76 | 131 |
| Private API calls | 4 `_list_raw()` | 0 (all public API) |
| Type safety | Hand-rolled IntEnum (wrong values) | Upstream string enum |
| Dead code removed | -- | ~710 lines (old orchestrator + legacy pipeline) |
| State persistence | None | JSON after each stage |
| Resumability | None | `--resume` flag |
| Dry-run | None | `--dry-run` flag |

## Prevention Strategies

### 1. Never Duplicate Upstream Types

Always import enums, constants, and type codes from the upstream library. Never hand-roll copies that can drift.

```python
# Wrong: hand-rolled copy
class ArtefactType(IntEnum):
    VIDEO = 8  # WRONG

# Right: import from upstream
from notebooklm import ArtifactType  # string enum, always correct
```

### 2. Validate at System Boundaries

Any user-provided path must be validated immediately. Never pass raw strings to `Path()` joins or destructive operations.

```python
def _validate_store_slug(slug: str) -> None:
    if slug.startswith(("/", "~")):
        raise StoreError(f"Must be org/repo, not a path: {slug}")
```

### 3. Safe Defaults for Destructive Operations

Default to the least destructive behaviour. Require explicit opt-in for anything that deletes data.

```python
# Wrong: deletes everything by default
_delete_existing_by_type(artefact, failed_only=False)

# Right: only deletes failed by default
for art in artifacts:
    if art.kind == target_kind and art.is_failed:
        await client.artifacts.delete(nb_id, art.id)
```

### 4. Use Public APIs Only

Private methods (`_list_raw`, `_artifacts`) are internal contracts that break without notice. The public API (`artifacts.list()`) returns parsed objects with stable properties.

### 5. Stage Gates Between Async Operations

Never assume an async operation completed. Always verify with a pre-check before the next stage.

```python
# Upload stage uses wait=True
await client.sources.add_file(nb_id, path, wait=True, wait_timeout=180.0)

# Generate stage pre-check verifies notebook_id exists
def pre_check(self, ctx):
    if not ctx.state.notebook_id:
        return StageResult(Status.FAIL, "No notebook ID")
```

## Files Changed (17 commits)

| File | Change |
|------|--------|
| `src/repo_artefacts/pipeline.py` | +672 lines -- new stage-based pipeline |
| `src/repo_artefacts/cli.py` | Rewired generate/publish through safe stages, deleted legacy |
| `src/repo_artefacts/notebooklm.py` | -450 lines -- deleted old orchestrator, adopted upstream API |
| `src/repo_artefacts/store.py` | +60 lines -- slug validation, safe_rmtree |
| `tests/test_pipeline.py` | +641 lines -- 65 stage tests |
| `tests/test_store.py` | +60 lines -- 10 store safety tests |
| `tests/test_notebooklm.py` | -138 lines -- removed tests for deleted functions |
| `docs/pipeline.md` | Full rewrite with Mermaid diagrams |

## Related Resources

- [Pipeline documentation](../pipeline.md) -- Mermaid flowchart and sequence diagram
- [Brainstorm](../brainstorms/2026-03-15-pipeline-restructure-brainstorm.md) -- Design decisions
- [Upstream deviation report](../../UPSTREAM_DEVIATION_REPORT.md) -- Full audit against notebooklm-py
