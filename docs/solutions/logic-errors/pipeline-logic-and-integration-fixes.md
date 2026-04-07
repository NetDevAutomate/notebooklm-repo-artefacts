---
title: "Pipeline Multi-Blocker Fixes: post_check, CLI passthrough, quota detection, source readiness, SSH auth, logging"
slug: pipeline-logic-and-integration-fixes
category: logic-errors
date: 2026-04-07
severity: critical
components:
  - src/repo_artefacts/pipeline.py (GenerateStage.post_check, CleanupStage.pre_check, run_pipeline)
  - src/repo_artefacts/cli.py (pipeline command)
  - src/repo_artefacts/notebooklm.py (_is_quota_error, upload_repo, generate_artefacts)
  - src/repo_artefacts/store.py (clone_or_pull_store)
  - src/repo_artefacts/collector.py (logging)
  - src/repo_artefacts/publish.py (logging)
symptoms:
  - Partial runs (--audio --video) always failed post_check
  - --notebook-id CLI flag had no effect on pipeline execution
  - Quota errors with error=None but error_code=USER_DISPLAYABLE_ERROR went undetected
  - Generation requests returned "no artifact_id returned" immediately after PDF upload
  - Silent auth failures when cloning artefact store via HTTPS on macOS
  - --resume re-ran all stages including already-passed ones
  - No persistent log file for debugging pipeline failures
root_causes:
  - post_check validated all 4 artefact types unconditionally instead of only selected types
  - CLI --notebook-id was parsed but never forwarded to run_pipeline()
  - Quota detection only checked error message strings, not error_code field
  - No wait/poll for source.is_ready after PDF upload before triggering generation
  - Store clone used HTTPS URL which lacks silent credential support on macOS
  - Resume loaded state but never consulted it in the stage loop
  - No FileHandler configured; all debug traces lost after process exit
resolution_type: code_fix
---

# Pipeline Multi-Blocker Fixes

## Problem

Seven distinct failure modes prevented reliable end-to-end pipeline runs:

1. **post_check over-specification**: Completion check validated all 4 artefact types regardless of `--audio`/`--video` selection, causing every partial run to fail.
2. **notebook_id dropped**: CLI accepted `--notebook-id` but `run_pipeline()` had no parameter for it — the value was silently discarded.
3. **Quota detection blind spot**: `_is_quota_error()` only matched message strings. The API also signals quota via `error_code == "USER_DISPLAYABLE_ERROR"` with `error=None`.
4. **Source readiness race**: `upload_repo()` proceeded to generation immediately after `add_file()`. NotebookLM indexes uploads asynchronously — generation against unready sources returned no `artifact_id`, triggering 3+ retries per run.
5. **HTTPS auth failure**: Store clone used HTTPS unconditionally. On macOS without a token, this failed silently.
6. **Resume ignored state**: `--resume` loaded `.pipeline-state.json` but the stage loop didn't skip already-passed stages.
7. **No persistent logging**: Console output was the only diagnostic — lost after terminal close.

## Root Cause

Each issue had a distinct root cause but a common theme: **assumptions that were never validated**.

- post_check assumed the target set was always "all artefacts"
- CLI assumed parameter parsing was sufficient (no end-to-end trace)
- Quota detection assumed the error message was always populated
- Upload assumed the source was immediately ready
- Store assumed HTTPS auth worked everywhere
- Resume assumed loading state was the same as using it
- Logging assumed console output was sufficient

## Solution

### Fix 1 — post_check artefact selection (`pipeline.py`)

```python
# BEFORE: checked all types
def post_check(self, ctx):
    all_done = all(ctx.state.artefacts.get(name) == "completed" for name in ARTEFACT_CONFIG)

# AFTER: checks only selected types
def post_check(self, ctx):
    target = ctx.artefact_selection or list(ARTEFACT_CONFIG)
    all_done = all(ctx.state.artefacts.get(name) == "completed" for name in target)
```

Same fix applied to `CleanupStage.pre_check`.

### Fix 2 — notebook_id passthrough (`pipeline.py` + `cli.py`)

```python
# pipeline.py: added parameter
def run_pipeline(..., notebook_id: str | None = None):
    state = PipelineState.load(state_path) if resume else PipelineState()
    if notebook_id:
        state.notebook_id = notebook_id

# cli.py: wired through
ok = run_pipeline(..., notebook_id=notebook_id)
```

### Fix 3 — Quota detection (`notebooklm.py`)

```python
# BEFORE
def _is_quota_error(error_msg: str) -> bool:
    lower = error_msg.lower()
    return any(p in lower for p in QUOTA_ERROR_PATTERNS)

# AFTER
def _is_quota_error(error_msg: str | None, error_code: str | None = None) -> bool:
    if error_code and error_code.upper() == "USER_DISPLAYABLE_ERROR":
        return True
    if error_msg:
        lower = error_msg.lower()
        return any(p in lower for p in QUOTA_ERROR_PATTERNS)
    return False
```

### Fix 4 — Source readiness polling (`notebooklm.py`)

```python
# After add_file(), poll source.is_ready before returning
max_wait = 120  # seconds
poll_interval = 5
elapsed = 0
while elapsed < max_wait:
    sources = await client.sources.list(nb_id)
    if all(s.is_ready for s in sources):
        break
    if any(s.is_error for s in sources):
        break  # log error
    await asyncio.sleep(poll_interval)
    elapsed += poll_interval
```

**Impact**: Zero generation retries after this fix vs 3+ retries before.

### Fix 5 — SSH clone (`store.py`)

```python
# BEFORE
clone_url = f"https://github.com/{store_slug}.git"

# AFTER
clone_url = f"git@github.com:{store_slug}.git"
```

Token auth preserved as explicit override.

### Fix 6 — Resume stage skipping (`pipeline.py`)

```python
# Added at top of stage loop
if resume and ctx.state.stage_status(stage.name) == "pass":
    logger.info("Stage %s: already passed — skipping (resume)", stage.name)
    continue
```

### Fix 7 — Diagnostic logging (all modules)

- `FileHandler` in `run_pipeline()` writes to `.pipeline.log`
- `logger = logging.getLogger(__name__)` in `notebooklm.py`, `collector.py`, `publish.py`
- Debug-level: API responses (task_id, status, error, error_code), poll cycles, retry decisions, auth refreshes, stage transitions

## Tests Added

27 new tests (133 -> 160 total):

- 4 `GenerateStage` tests for partial/single/none artefact selection
- 3 `CleanupStage` tests for partial selection and quota_exhausted
- 4 `LocalPublishStage`/`LocalVerifyStage` pre_check tests
- 2 `run_pipeline` integration tests verifying notebook_id passthrough
- 14 `_is_quota_error` tests covering message matching, error_code, None handling, case sensitivity

## Prevention Strategies

### 1. Artefact Selection — Single Source of Truth

Any stage that filters or checks artefacts **must** read from `ctx.artefact_selection`. In code review, flag any stage that constructs its own filtered list.

### 2. CLI Parameters — Trace End-to-End at Definition Time

When adding a CLI parameter, write the full call chain immediately: CLI arg -> parse -> context -> execution. Add a test that passes a non-default value and asserts it reaches execution.

### 3. API Error Detection — Check All Signal Fields

When writing API response handlers, enumerate **all** fields the API uses to signal failure (error, error_code, status) and check all of them. Write parametrised tests for each error signalling pattern.

### 4. External Service Timing — Poll, Don't Sleep

Any wait on an external service must be a poll loop with a condition and timeout, not a fixed sleep. Add a comment explaining what is being waited for and what fails without it.

### 5. Auth Transport — Validate at Setup

Document the required auth method and add a preflight check that validates it before the pipeline starts.

### 6. State Load Must Gate Execution

The pattern `load_state(); for stage in stages: run(stage)` is always wrong if resume is a requirement. Write the skip condition at the same time as the state load.

### 7. Persistent Logs Are Non-Negotiable

Wire up file logging before writing pipeline logic. Treat "I had to re-run it to see what happened" as a process failure.

## Related Documentation

- [Pipeline restructure learnings](../2026-03-15-pipeline-restructure-learnings.md) — original stage-based pipeline design
- [Resume stage-skipping fix](resume-skips-completed-pipeline-stages.md) — detailed resume fix documentation
- [docs/TODO.md](../../TODO.md) — P3.1 (notebook-id), P1.1 (runner coverage), P1.2 (LocalPublish/Verify tests)
- **Upstream**: notebooklm-py PR #240 — fixes `poll_status()` returning "pending" for quota-removed artefacts (merged, unreleased as of v0.3.4)
