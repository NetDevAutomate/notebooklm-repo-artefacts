# TODO: Code Review Remediation

> Generated from thorough code review on 2026-04-04.
> All 131 existing tests pass. This document tracks gaps and improvements.

---

## Priority 1 — Critical (Missing Tests)

### 1.1 Test `run_pipeline()` runner loop
**Problem**: The core pipeline runner (`pipeline.py:583-706`) has zero test coverage. No test verifies stage sequencing, break-on-fail, dry-run mode, resume logic, or state persistence across stages.

**What to test**:
- [ ] Stages execute in correct order when all pass
- [ ] Pipeline breaks on first stage failure
- [ ] Pipeline breaks on pre-check failure
- [ ] Pipeline breaks on post-check failure
- [ ] Pipeline breaks on execute exception
- [ ] Dry-run mode skips all execution, marks stages as "dry_run"
- [ ] Resume mode loads previous state and skips completed stages
- [ ] State is persisted after each successful stage
- [ ] State file path is correct (output dir, not cwd)
- [ ] Returns `True` when all pass, `False` when any fail
- [ ] Duration tracking is recorded per stage
- [ ] Notification is sent on completion/failure (mock `osascript`)

**Approach**: Mock all stage `execute()`, `pre_check()`, `post_check()` methods. Use `unittest.mock.patch` to replace stage classes with controlled doubles.

### 1.2 Test `LocalPublishStage` and `LocalVerifyStage`
**Problem**: These stages have zero pre/post check tests.

**What to test**:
- [ ] `LocalPublishStage.pre_check` — SKIP when `store_slug` is set
- [ ] `LocalPublishStage.pre_check` — PASS when `store_slug` is None
- [ ] `LocalVerifyStage.pre_check` — SKIP when `store_slug` is set
- [ ] `LocalVerifyStage.pre_check` — PASS when `store_slug` is None

### 1.3 Add coverage enforcement to CI
**Problem**: `pytest-cov` is a dev dependency but never used in CI. No visibility into what's untested.

**What to do**:
- [ ] Add `--cov=repo_artefacts --cov-report=term-missing` to `uv run pytest` in CI
- [ ] Add `--cov-fail-under=80` (or agreed threshold) to `.github/workflows/ci.yml`
- [ ] Add coverage to pre-commit hook or keep in CI only (pre-commit is slow with coverage)

---

## Priority 2 — Important (CI/CD Pipeline)

### 2.1 Nightly upstream dependency test workflow
**Problem**: No automated testing against new `notebooklm-py` releases.

**What to build** (`.github/workflows/nightly-deps.yml`):
- [ ] Trigger: `schedule: cron: '0 2 * * *'` (2am UTC daily) + `workflow_dispatch`
- [ ] Step 1: Install latest `notebooklm-py` (temporarily remove `<0.4` upper bound)
- [ ] Step 2: Run full test suite (`uv run pytest -v --cov=repo_artefacts`)
- [ ] Step 3: Run lint (`uv run ruff check`, `uv run pyright`)
- [ ] Step 4: If all pass → open PR bumping `notebooklm-py` version in `pyproject.toml`
- [ ] Step 5: If any fail → send email to `andy@andytaylor.dev` with failure details
- [ ] Include: Python version matrix (3.11, 3.12, 3.13)
- [ ] Include: Link to failing test output in the email/PR

**Email delivery options**:
- Use GitHub Actions `actions/github-script` to call SendGrid/AWS SES API
- Or use a simple SMTP action with GitHub secrets for credentials
- Or use a webhook to a notification service (Slack, Discord, etc.)

### 2.2 Regression test strategy
**Problem**: No explicit regression testing when upstream changes.

**What to add**:
- [ ] Snapshot test: capture expected output of key functions, compare on each run
- [ ] Contract test: verify `notebooklm-py` public API surface hasn't changed (expected classes, methods, signatures)
- [ ] Integration smoke test: mock the full pipeline end-to-end with stubbed API responses

---

## Priority 3 — Code Quality Fixes

### 3.1 Fix dead `--notebook-id` parameter in `pipeline` command
**File**: `cli.py:371-377`

**Problem**: The `pipeline` command accepts `--notebook-id` but never passes it to `run_pipeline()`. The value is silently ignored.

**Fix**: Either wire it through to set `ctx.state.notebook_id` before running, or remove the parameter and document that `NOTEBOOK_ID` env var is used by the upload stage's existing notebook lookup.

### 3.2 Add `--force-regen` CLI flag
**File**: `cli.py:454-463`

**Problem**: `force_regen=False` is hardcoded. No way to force regeneration from the CLI.

**Fix**: Add `--force-regen` boolean option to the `pipeline` command and pass it through.

### 3.3 Make stage list conditional instead of using SKIP gates
**File**: `pipeline.py:531-541`

**Problem**: `ALL_STAGES` includes both `PublishStage` AND `LocalPublishStage`, both `VerifyStage` AND `LocalVerifyStage`. They use SKIP gates to avoid running both, but this is implicit coupling and makes the stage order hard to reason about.

**Fix**: Build the stage list dynamically in `run_pipeline()` based on `store_slug` presence:
```python
stages = [CollectStage(), UploadStage(), GenerateStage(), DownloadStage()]
if store_slug:
    stages.extend([PublishStage(), VerifyStage(), ReadmeStage()])
else:
    stages.extend([LocalPublishStage(), LocalVerifyStage()])
stages.append(CleanupStage())
```

### 3.4 Add timeouts to all `subprocess.run()` calls
**Files**: `pipeline.py`, `cli.py`, `publish.py`, `store.py`

**Problem**: Several `subprocess.run()` calls lack `timeout=` parameters. A hung git operation would hang the pipeline indefinitely.

**Fix**: Add `timeout=30` (or appropriate value) to all `subprocess.run()` calls. Catch `subprocess.TimeoutExpired` and translate to a domain exception.

### 3.5 Fix Rich `print()` invalid kwarg
**File**: `publish.py:108`

**Problem**: `get_console().print("...", style="dim")` — `style` is not a valid kwarg for Rich's `print()`. Should be `"[dim]...[/dim]"` markup.

### 3.6 Improve `PipelineState` serialization
**File**: `pipeline.py:77-89`

**Problem**:
- `save()` uses `self.__dict__` — fragile if dataclass gains non-serializable fields
- `load()` uses `setattr` — bypasses type safety

**Fix**: Use explicit serialization:
```python
def save(self, path: Path) -> None:
    self.updated_at = datetime.now(UTC).isoformat()
    data = {
        "repo_name": self.repo_name,
        "notebook_id": self.notebook_id,
        "content_hash": self.content_hash,
        "source_replaced": self.source_replaced,
        "stages": self.stages,
        "artefacts": self.artefacts,
        "started_at": self.started_at,
        "updated_at": self.updated_at,
    }
    path.write_text(json.dumps(data, indent=2) + "\n")
```

### 3.7 Fix relative state path default
**File**: `pipeline.py:118`

**Problem**: `state_path` default uses `Path(STATE_FILENAME)` — a relative path that depends on cwd. Could write to unexpected locations.

**Fix**: Don't provide a default. Require `state_path` to be set explicitly by the caller (which `run_pipeline()` already does).

---

## Priority 4 — Robustness Improvements

### 4.1 Add `doctor` / health check command
**What**: A `repo-artefacts doctor` command that validates:
- notebooklm-py version and importability
- Auth status (can we create a NotebookLMClient?)
- Playwright/Chromium availability
- Git config (user.name, user.email)
- GitHub token availability
- Store connectivity (if configured)
- Python version compatibility

### 4.2 Add structured JSON output mode
**What**: Add `--json` flag to `pipeline` command for CI consumption. Output machine-readable result:
```json
{
  "repo_name": "my-repo",
  "status": "success",
  "stages": {
    "collect": {"status": "pass", "duration_s": 2.3},
    "upload": {"status": "pass", "duration_s": 5.1},
    ...
  },
  "total_duration_s": 45.2,
  "artefacts": {"audio": "completed", "video": "completed", ...}
}
```

### 4.3 Pin notebooklm-py more precisely
**Current**: `notebooklm-py[browser]>=0.3.4,<0.4`

**Options**:
- Use compatible release: `~=0.3.4` (allows `0.3.x` but not `0.4`)
- Pin exact version with automated bump PRs from nightly workflow
- Both: pin exact in `uv.lock`, use `~=0.3` in `pyproject.toml`

### 4.4 Add missing test modules
- [ ] `tests/test_config.py` — `load_config()`, `save_config()` roundtrip, missing file, invalid TOML
- [ ] `tests/test_exceptions.py` — exception hierarchy, string representation, inheritance
- [ ] `tests/test_collector.py` — `render_to_pdf()` (mock md2pdf import)

---

## Priority 5 — Nice to Have

### 5.1 Add retry strategy for `UploadStage`
Currently if upload fails, the pipeline aborts immediately. Consider adding retry with backoff for transient failures.

### 5.2 Add pipeline metrics
Track and report: total duration, per-stage duration, artefact generation success rate, quota exhaustion frequency.

### 5.3 Add `--stage` flag to run a single stage
For debugging: `repo-artefacts pipeline --stage generate --resume`

### 5.4 Add pre-flight validation
Before starting the pipeline, validate all prerequisites (auth, tokens, disk space, network) in a single "preflight" stage.
