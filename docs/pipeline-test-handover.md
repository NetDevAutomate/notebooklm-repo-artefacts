# Pipeline Test Handover

> Context for the next session to test the full pipeline end-to-end.

## What Changed

### Code Changes
- **Upgraded notebooklm-py** from 0.3.3 → 0.3.4+ (media-readiness checks, str enum types)
- **Eliminated raw API parsing** — replaced `RawArtefact`, `_parse_raw_artefacts`, `ArtefactType` IntEnum, `_poll_by_type`, `_snapshot_artefact_ids` with upstream public APIs
- **Added `LocalPublishStage` and `LocalVerifyStage`** — local mode now properly sets up GitHub Pages, commits artefacts, pushes, and verifies deployment (was a gap before)
- **Added `Last-Modified` freshness check** in `verify_pages()` — detects stale cached files vs fresh deployments
- **Added pyright type stubs** for `notebooklm` and `md2pdf` packages

### Documentation
- Updated `codemap.md` with full architecture layers, stage pattern, key design patterns
- Updated `how-it-works.md` with new generation flow and retry logic
- Updated `pipeline.md` with correct local vs store mode stage breakdown
- Updated `troubleshooting.md` with auth expiry, pipeline resume, and quota exhaustion sections
- Created `architecture-deep-dive.md` with 8 detailed sections and code snippets

## Current State

### What Works
- ✅ All 131 tests passing
- ✅ Ruff lint: all checks passed
- ✅ Pyright: 0 errors
- ✅ Code committed and pushed to `main`

### What Needs Testing
- ❌ **Full pipeline end-to-end** — never been run with the new code
- ❌ **Local mode** — `LocalPublishStage` and `LocalVerifyStage` are new, untested
- ❌ **Store mode** — `PublishStage` and `VerifyStage` need testing with `--store`
- ❌ **Resume flow** — `--resume` after partial failure needs testing
- ❌ **Freshness check** — `Last-Modified` header verification in `verify_pages()`

## Test Plan

### Prerequisites
1. **Auth**: Ensure `notebooklm login` is valid (cookies not expired)
2. **GitHub token**: `GITHUB_TOKEN` env var or stored in `~/.config/secrets/tokens.age`
3. **Test repo**: A git repo with some content (README, docs, source files)
4. **Store repo** (for store mode test): An empty public GitHub repo

### Test 1: Local Mode (Fresh Run)

```bash
cd /path/to/test-repo
repo-artefacts pipeline .
```

**Expected flow:**
1. `collect` — scans repo, renders PDF
2. `upload` — uploads to NotebookLM, gets notebook_id
3. `generate` — generates all 4 artefacts (audio, video, slides, infographic)
   - Uses upstream `wait_for_completion()` with exponential backoff
   - Media-readiness checks before reporting COMPLETED
4. `download` — downloads artefacts to `docs/artefacts/`
5. `publish` — **SKIPPED** (no store configured)
6. `local_publish` — sets up GitHub Pages, commits artefacts, pushes
7. `verify` — **SKIPPED** (store mode only)
8. `local_verify` — polls Pages URL until 200, checks Last-Modified headers
9. `cleanup` — deletes notebook

**What to watch for:**
- Slides generation should now work (VIDEO/SLIDES swap bug is fixed)
- `Last-Modified` timestamps should show files from this run (not stale)
- Artefacts should be committed to `docs/artefacts/` and pushed
- GitHub Pages should be enabled and serving files

### Test 2: Resume After Partial Failure

If Test 1 fails at any stage (e.g. slides quota exhausted):

```bash
repo-artefacts pipeline . --resume
```

**Expected:**
- Skips stages that already passed (check `.pipeline-state.json`)
- Only retries failed stages
- Does not re-upload or re-generate completed artefacts

### Test 3: Store Mode

```bash
repo-artefacts pipeline . --store NetDevAutomate/artefact-store
```

**Expected flow:**
1. `collect` → `upload` → `generate` → `download` (same as local)
2. `publish` — clones store repo, copies artefacts, pushes
3. `local_publish` — **SKIPPED** (store mode)
4. `verify` — polls store Pages URL, checks Last-Modified
5. `local_verify` — **SKIPPED** (store mode)
6. `cleanup` — deletes notebook

**What to watch for:**
- Store repo gets artefacts + player page + manifest.json
- Source repo gets only README links (no binary files)
- Store Pages URL serves artefacts correctly

### Test 4: Artefact Selection

```bash
# Only generate audio and video
repo-artefacts pipeline . --audio --video

# Generate all except infographic
repo-artefacts pipeline . --exclude infographic
```

**Expected:**
- Only requested artefact types are generated
- Other types are not requested from NotebookLM
- Pipeline completes successfully with partial artefact set

## Known Issues / Decisions

### Removed ReadmeStage
The old `ReadmeStage` was removed because:
- In local mode, `LocalPublishStage` already calls `setup_pages()` which updates README
- In store mode, the README update is handled by the `publish` command in `cli.py` (not the pipeline)
- **Decision needed**: Should store mode also update the source README via a stage, or is the CLI `publish` command sufficient?

### Local Mode Verification URL
`LocalVerifyStage` constructs the Pages URL as:
```python
base_url = f"https://{org.lower()}.github.io/{repo_name}/artefacts/"
```
This assumes GitHub Pages is served from `username.github.io/repo/artefacts/`. If the repo uses a custom domain (CNAME), this won't work. **Fix needed**: Check for CNAME in the repo's Pages settings or use the GitHub API to get the actual Pages URL.

### Timeout Defaults
- Generation timeout: 900s (15 min) per artefact
- Verification timeout: 120s (2 min) for Pages URL
- Cinematic videos can take 30-40 minutes — may need `--timeout 2400`

## File Locations

| File | Purpose |
|------|---------|
| `src/repo_artefacts/pipeline.py` | Stage definitions + runner |
| `src/repo_artefacts/notebooklm.py` | NotebookLM API wrapper |
| `src/repo_artefacts/publish.py` | verify_pages() with freshness check |
| `src/repo_artefacts/cli.py` | CLI commands (pipeline delegates to run_pipeline) |
| `docs/pipeline.md` | Pipeline architecture docs |
| `docs/architecture-deep-dive.md` | Detailed code walkthrough |
| `stubs/notebooklm/__init__.pyi` | Type stubs for notebooklm-py |

## State File

Pipeline state is saved to `docs/artefacts/.pipeline-state.json`. To force a fresh run, delete it:

```bash
rm docs/artefacts/.pipeline-state.json
```

## NotebookLM Quota Limits

NotebookLM enforces separate daily caps per artefact type:
- **Audio**: ~50-100/day (Pro)
- **Video**: ~20-25/day (Pro) — cinematic videos take longer
- **Slides**: ~20-25/day (Pro)
- **Infographic**: ~20-25/day (Pro)

Caps reset 24h from first daily use (UTC). If you hit a quota limit, the pipeline marks that artefact as `quota_exhausted` and continues with the others. Use `--resume` the next day to retry.
