---
title: Resume flag ignored loaded pipeline state, re-running completed stages
date: 2026-04-06
category: logic-errors
tags: [pipeline, resume, state-management, cli, idempotency, stage-runner]
component: src/repo_artefacts/pipeline.py — run_pipeline() stage runner loop
severity: medium
symptoms:
  - Resume re-collected local repo docs (filesystem walk + file reads)
  - Resume re-rendered content to PDF via Playwright/Chromium
  - Resume re-uploaded to NotebookLM and re-polled for source readiness
  - All previously completed stages executed again unnecessarily
root_cause: Stage runner loop iterated ALL_STAGES without consulting loaded .pipeline-state.json to skip already-passed stages
---

# Resume flag ignored loaded pipeline state

## Problem

When `--resume` was used after a pipeline failure, **all stages re-ran from scratch** — including expensive operations like collecting repo documentation, rendering a PDF with Playwright/Chromium, and uploading to NotebookLM with polling for source readiness.

The user expectation: resume picks up from the first incomplete stage. The actual behaviour: resume re-did everything.

## Root Cause

The runner loop at `pipeline.py:666` iterated `ALL_STAGES` unconditionally. On `--resume`, line 621 correctly loaded persisted state:

```python
state = PipelineState.load(state_path) if resume else PipelineState()
```

And the `stage_status()` method existed (lines 92-93) to query per-stage status. But **nothing in the runner loop ever called it**. The infrastructure for skipping was present — the actual skip logic was missing.

This is a classic "state persisted but not consumed" bug. The write path was complete; the read-back path had a gap.

## Solution

A five-line block inserted at lines 677-681, between the `dry_run` guard and the `pre_check` call:

```python
# Resume: skip stages that already passed in a previous run
if resume and ctx.state.stage_status(stage.name) == "pass":
    logger.info("Stage %s: already passed — skipping (resume)", stage.name)
    console.print("  [dim]Already passed — skipping (resume)[/dim]")
    continue
```

Two-condition AND:
1. `resume` — only activates on explicit resume; normal runs unaffected
2. `ctx.state.stage_status(stage.name) == "pass"` — only skips stages that completed successfully

## Why This Approach

- **Minimal and non-invasive** — one check in the runner loop handles all stages uniformly
- **No per-stage modifications** — no changes to any stage class
- **No new state fields or methods** — uses existing `stage_status()` infrastructure
- **Consistent pattern** — placed after the `dry_run` guard which also uses `continue`

## Edge Cases

| Prior status | Behaviour on resume | Correct? |
|---|---|---|
| `"pass"` | Skipped (the fix) | Yes — already completed |
| `"failed"` / `"error"` | Re-executes via pre_check + execute | Yes — retry the failure |
| `"skipped"` | Re-evaluates via pre_check | Yes — conditions may have changed |
| `"dry_run"` | Re-executes | Yes — dry run didn't actually do anything |
| `""` (no entry) | Executes normally | Yes — never attempted |

## Prevention Strategies

### Write the consumer before the producer

When implementing state persistence, write the code that reads and acts on the state at the same time as the code that writes it. If the skip logic had been written alongside `save_state`, the gap would never have opened.

### Implementation checklist for persist/resume features

```
[ ] State is serialised correctly (save path)
[ ] State is deserialised correctly (load path)
[ ] Loaded state is consulted in the decision loop (consume path)
[ ] Consuming the state produces observable behaviour change (effect path)
[ ] Each path above has at least one test
```

### Test the round-trip, not just the halves

Unit tests for `save_state` and `load_state` in isolation are necessary but insufficient. The integration test must:

1. Run the pipeline to partial completion
2. Assert the state file reflects what happened
3. Create a fresh pipeline with the same state file and `resume=True`
4. Assert completed stages do not re-execute
5. Assert incomplete stages do execute

Step 3 is the one most commonly missing.

## Related

- [docs/pipeline.md](../pipeline.md) — CLI reference and `--resume` usage examples
- [docs/architecture.md](../architecture.md) — Pre/execute/post-check pattern and `PipelineState` design
- [docs/troubleshooting.md](../troubleshooting.md#pipeline-resume-issues) — Known resume failure modes
- [2026-03-15 Pipeline restructure learnings](../2026-03-15-pipeline-restructure-learnings.md) — Prior learnings covering idempotency layers
