# Brainstorm: Pipeline Robustness, Interactive Fallback, and Full Improvement Sweep

**Date:** 2026-03-10
**Status:** Brainstorm complete, ready for planning

## What We're Building

A comprehensive improvement to repo-artefacts covering three areas:

### 1. Artefact Freshness: Always Re-download

When the pipeline or publish command runs, always download completed artefacts from NotebookLM, overwriting local files. This ensures the user always has the latest version. No timestamp comparison needed -- simple and reliable.

**Current behavior:** Downloads whatever is completed, but `--resume` skips types that are already completed in the notebook (not on disk). Individual `download` command always downloads.

**New behavior:** Pipeline always re-downloads all completed artefacts, regardless of local file state.

### 2. Interactive Fallback on Generation Failure (TTY-Aware)

When generation fails for an artefact type and the process is running in a TTY, prompt the user through a decision tree instead of silently continuing.

**Decision tree:**

```
Generation fails for {type} (e.g. Infographic)
  |
  +--> [TTY?] No --> Continue without (current behavior)
  |
  +--> [TTY?] Yes --> "Infographic generation failed. Try manually via NotebookLM UI? [Y/n]"
        |
        +--> N --> (go to local file check below)
        |
        +--> Y --> "Was the manual generation successful? [y/N]"
              |
              +--> Y --> Download from NotebookLM, continue
              |
              +--> N --> Local file check:
                    |
                    +--> Local exists --> "Use existing local Infographic: infographic.png? [Y/n]"
                    |     |
                    |     +--> Y --> Keep local, continue
                    |     +--> N --> Delete local, continue without
                    |
                    +--> No local --> Continue without
```

**TTY detection:** Use `sys.stdin.isatty()`. When non-TTY (CI/piped), fall back to current non-interactive behavior (fail fast, continue without).

**Dynamic output:** The README artefacts table and GitHub Pages player already dynamically hide missing artefact types. No changes needed there.

### 3. Infographic as Page Branding

Use the existing `docs/artefacts/infographic.png` as a hero background or header image on the GitHub Pages player page. The template JS already detects whether the infographic exists, so this can degrade gracefully.

**Approach:** Add the infographic as a subtle background to the hero section (low opacity, blurred) when available. The infographic card still shows the full image. This gives the page a unique, project-specific visual identity without requiring a separate image file.

### 4. Robustness Fixes

| Fix | Impact |
|-----|--------|
| Check `git_commit_and_push` return value | Prevents silent push failures leading to verify timeout |
| Check `enable_github_pages` return value | Surface Pages enablement failures |
| Add `_handle_errors` to `generate` and `download` commands | Clean error messages instead of raw tracebacks |
| Regenerate stale `docs/artefacts/index.html` on every `pages` run | Template changes (like clickable infographic) propagate automatically |
| Add `--version` flag | Standard CLI convention |
| Add `--quiet` flag | Wire up existing `configure_console(quiet=True)` |

### 5. Async Pipeline Consolidation

Refactor the `pipeline` command from 6+ `asyncio.run()` calls to a single `async def _run_pipeline()` function sharing one `NotebookLMClient` session.

**Benefits:**
- Single auth session (no repeated cookie loads)
- Faster execution (no event loop setup/teardown overhead)
- Cleaner error handling (one try/except around the whole flow)
- Foundation for future parallel generation requests

**Scope:** Only the `pipeline` command. Individual CLI commands (`generate`, `download`, etc.) keep their own `asyncio.run()` -- they're single operations.

### 6. CLI-Level Test Coverage

Add `typer.testing.CliRunner` tests for:
- `pipeline` command's conditional notebook deletion logic
- Artefact selection resolution (4 modes: all, explicit, exclude, resume)
- `_handle_errors` decorator behavior
- New interactive fallback prompts (mock TTY detection)

## Why This Approach

- **Always re-download** is simpler than timestamp comparison and avoids the API limitation (no artefact-level timestamps). The pipeline runs infrequently enough that re-downloading is negligible cost.
- **TTY auto-detection** is the standard Unix convention. No flag needed in the common case. Users running interactively get prompts; CI gets fail-fast.
- **Infographic as branding** reuses an existing asset instead of requiring users to add a separate image. Graceful degradation means it works on repos without an infographic too.
- **Single async pipeline** is the right architecture -- the current pattern of creating/destroying 6+ event loops is a known anti-pattern. Individual commands stay simple.

## Key Decisions

1. **Freshness strategy:** Always re-download (no timestamp comparison)
2. **Interactive mode:** TTY auto-detection via `sys.stdin.isatty()`
3. **Image source:** Existing infographic.png as hero background (no separate ./images dir)
4. **Async refactor:** Full consolidation in pipeline; individual commands unchanged
5. **Scope:** Full improvement sweep including robustness, async, UX, and tests

## Implementation Priority

| Phase | Items | Risk |
|-------|-------|------|
| 1. Robustness | Return value checks, `_handle_errors`, stale index.html, `--version`, `--quiet` | Low -- small targeted fixes |
| 2. Interactive fallback | TTY detection, prompt flow, fallback logic | Medium -- new user interaction patterns |
| 3. Always re-download | Modify pipeline/publish download behavior | Low -- simplifies existing logic |
| 4. Infographic branding | Template hero background, CSS changes | Low -- cosmetic, graceful degradation |
| 5. Async consolidation | Single async pipeline function | High -- largest refactor, touches core flow |
| 6. CLI tests | CliRunner tests for pipeline, selection, errors, prompts | Low -- additive, no production code changes |

## Open Questions

*None -- all key decisions resolved during brainstorming.*
