# Upstream Deviation Report: repo-artefacts wrapper vs notebooklm-py 0.3.4

Generated: 2026-03-15

Wrapper: `/src/repo_artefacts/notebooklm.py` (735 lines)
Upstream: `notebooklm-py` 0.3.4 (installed in `.venv`)

---

## 1. Private API Access (6 occurrences)

### 1a. Importing from `notebooklm._artifacts` (private module)

| | Detail |
|---|---|
| **Our code** | Lines 20-25: `from notebooklm._artifacts import ArtifactStatus, ArtifactTypeCode` |
| **Upstream provides** | `ArtifactStatus` is publicly re-exported via `notebooklm.types` and `notebooklm.rpc.types`. `ArtifactTypeCode` is explicitly documented as internal ("Note: This is an internal enum. Users should use ArtifactType (str enum) from notebooklm.types for a cleaner API.") |
| **Risk** | **HIGH** -- `_artifacts` is a private module. Any refactor (rename, restructure) breaks our imports silently. The upstream `__all__` in `types.py` explicitly excludes `ArtifactTypeCode` from the public API. |
| **Action** | Import `ArtifactStatus` from `notebooklm` (it is in `__all__`). Replace `ArtifactTypeCode` usage with the public `ArtifactType` (str enum) where possible. For the integer-code operations that genuinely need `ArtifactTypeCode`, import from `notebooklm.rpc.types` (semi-public, re-exported through `notebooklm.rpc.__init__`). |

### 1b. Calling `client.artifacts._list_raw()` (4 call sites)

| | Detail |
|---|---|
| **Our code** | Lines 338, 373, 387, 402: `client.artifacts._list_raw(notebook_id)` used for deletion, snapshotting, completion checking, and polling. |
| **Upstream provides** | `client.artifacts.list(notebook_id)` returns `list[Artifact]` with parsed `.id`, `.kind` (ArtifactType str enum), `.status` (int), `.url`. Also `client.artifacts.list(notebook_id, artifact_type=ArtifactType.AUDIO)` for filtered listing. |
| **Risk** | **HIGH** -- `_list_raw` is a private method. Its return format (nested arrays with positional indices) is an implementation detail. If upstream changes array positions, our `RawArtefact.from_raw()` silently breaks. We already experienced this with the VIDEO/SLIDES swap bug. |
| **Action** | Replace all `_list_raw` + `_parse_raw_artefacts` with `client.artifacts.list()`. The returned `Artifact` objects have `.id`, `.kind`, `.status`, and `.is_completed` / `.is_failed` (via status codes). This eliminates our entire `RawArtefact` class and `_parse_raw_artefacts` function. |

---

## 2. Duplicated Logic

### 2a. `RawArtefact` dataclass + `_parse_raw_artefacts()` (lines 59-102)

| | Detail |
|---|---|
| **Our code** | Custom dataclass that parses raw API arrays by positional index: `id=arr[0]`, `type_code=arr[2]`, `status=arr[4]`. Includes `.is_completed`, `.is_failed`, `.type_name` properties. |
| **Upstream provides** | `Artifact` dataclass with `.id`, `.kind` (ArtifactType), `.status` (int matching ArtifactStatus), `.title`, `.url`, `.created_at`. `Artifact.from_api_response()` handles all parsing including variant detection (quiz vs flashcards). |
| **Risk** | **HIGH** -- This was the root cause of the VIDEO/SLIDES deletion bug. Our hardcoded array indices diverged from upstream's parsing logic. Every upstream API change to the array format risks silently breaking us. |
| **Action** | Delete `RawArtefact` and `_parse_raw_artefacts` entirely. Use `Artifact` objects from `client.artifacts.list()`. |

### 2b. `NAME_TO_TYPE` / `TYPE_TO_NAME` mapping dicts (lines 48-56)

| | Detail |
|---|---|
| **Our code** | Manual mapping: `{"audio": ArtifactTypeCode.AUDIO, "video": ArtifactTypeCode.VIDEO, ...}` using internal integer enum. |
| **Upstream provides** | `ArtifactType` (str enum) where `ArtifactType.AUDIO == "audio"`, `ArtifactType.VIDEO == "video"`, etc. Direct string comparison works: `artifact.kind == "audio"`. |
| **Risk** | **MEDIUM** -- These mappings use the internal `ArtifactTypeCode` (IntEnum). If upstream renumbers the codes (as happened with VIDEO=2->3), our mappings break. The upstream `ArtifactType` str enum is stable by design. |
| **Action** | Replace `NAME_TO_TYPE` with direct `ArtifactType` str enum comparisons. Replace `TYPE_TO_NAME` with `artifact.kind.value` (already a string). |

### 2c. Custom polling in `_poll_by_type()` (lines 395-420)

| | Detail |
|---|---|
| **Our code** | Custom poll loop using `_list_raw` + `_parse_raw_artefacts`, matching by type code and detecting new IDs. 30-second fixed interval. |
| **Upstream provides** | `client.artifacts.wait_for_completion(notebook_id, task_id, timeout=300, initial_interval=2.0, max_interval=10.0)` with exponential backoff. Also `client.artifacts.poll_status(notebook_id, task_id)` for single checks. Upstream also handles the media-URL-not-ready edge case (downgrades COMPLETED to PROCESSING if URL is not yet populated). |
| **Risk** | **MEDIUM** -- Our polling is coarser (30s fixed vs 2s-10s exponential) and misses the media-ready check. Our comment "Polls by artefact type (not task_id) because the NotebookLM API returns different IDs for generation tasks vs completed artefacts" suggests a historical bug that may have been fixed upstream. |
| **Action** | Test whether `wait_for_completion(task_id)` now works correctly with the generation task IDs. If so, replace our custom polling entirely. If the ID mismatch persists, use `client.artifacts.list()` with type filtering instead of `_list_raw`. |

---

## 3. Deviated Constants / Hardcoded Values

### 3a. No hardcoded type-code integers (GOOD -- already using upstream enums)

Our code correctly imports `ArtifactTypeCode` rather than hardcoding `1`, `3`, `7`, `8`. However, it imports from the private module path.

### 3b. Hardcoded poll interval: 30 seconds (line 532 equivalent)

| | Detail |
|---|---|
| **Our code** | `poll_interval = 30` -- fixed 30-second polling. |
| **Upstream provides** | Exponential backoff: `initial_interval=2.0`, `max_interval=10.0` in `wait_for_completion`. |
| **Risk** | **LOW** -- Our interval is intentionally conservative (multiple concurrent artifacts), but means slower completion detection (up to 30s latency vs ~2-10s upstream). |
| **Action** | Consider adopting exponential backoff, or at minimum reducing to 10-15 seconds. |

### 3c. Hardcoded timeout: 900 seconds (line 458 equivalent)

| | Detail |
|---|---|
| **Our code** | `timeout: int = 900` (15 minutes) for `generate_artefacts()`. |
| **Upstream provides** | `timeout: float = 300.0` (5 minutes) for `wait_for_completion()`. |
| **Risk** | **LOW** -- Our higher timeout is intentional (we generate up to 4 artifacts sequentially). Acceptable deviation. |
| **Action** | Keep, but document the rationale. |

### 3d. `QUOTA_ERROR_PATTERNS` pattern matching (lines 36-38)

| | Detail |
|---|---|
| **Our code** | `["rate limit", "quota exceeded", "quota"]` -- substring match on error messages. |
| **Upstream provides** | `RateLimitError` exception class (separate from `RPCError`). Upstream raises typed exceptions. |
| **Risk** | **MEDIUM** -- If upstream changes error message wording, our pattern matching fails silently. We already catch `RateLimitError` in `_with_reauth`, so the string matching in `_is_quota_error()` may be redundant or only catching edge cases. |
| **Action** | Audit whether `_is_quota_error()` is ever reached after `RateLimitError` is already caught. If so, rely on the typed exception instead. |

---

## 4. Missed Upstream Features

### 4a. `client.artifacts.list(notebook_id, artifact_type=ArtifactType.AUDIO)`

| | Detail |
|---|---|
| **Not used** | Upstream supports filtered listing by artifact type as a parameter. |
| **Our code** | Uses `_list_raw()` + manual filtering by `ArtifactTypeCode` integer. |
| **Risk** | **MEDIUM** -- We duplicate filtering logic that upstream already provides. |
| **Action** | Use the `artifact_type` parameter on `list()` for type-filtered queries. |

### 4b. `Artifact.is_completed` / `Artifact.is_failed` properties (if available)

| | Detail |
|---|---|
| **Not used** | The upstream `Artifact` has `.status` (int matching `ArtifactStatus` enum values). Direct comparison: `artifact.status == ArtifactStatus.COMPLETED`. |
| **Our code** | Reimplements these as properties on `RawArtefact`. |
| **Action** | Use upstream `Artifact.status` with `ArtifactStatus` comparison. |

### 4c. `client.artifacts.wait_for_completion()` with exponential backoff

| | Detail |
|---|---|
| **Not used** | Upstream provides a complete polling solution with configurable intervals, exponential backoff, and media-readiness checks. |
| **Our code** | Custom `_poll_by_type` with 30-second fixed interval, no media-readiness check. |
| **Action** | Evaluate replacing our custom poll loop. See item 2c. |

### 4d. `Artifact.kind` (str enum) for clean type comparisons

| | Detail |
|---|---|
| **Not used** | `artifact.kind == ArtifactType.AUDIO` or `artifact.kind == "audio"` -- string comparison, no integer codes needed. |
| **Our code** | Uses integer `ArtifactTypeCode` throughout. |
| **Action** | Migrate to `ArtifactType` str enum comparisons. |

### 4e. `client.artifacts.rename()` and `client.artifacts.export_report()`

| | Detail |
|---|---|
| **Not used** | Could be useful for post-generation workflows. |
| **Risk** | **LOW** -- Not needed for current functionality. |
| **Action** | Note for future feature additions. |

---

## 5. Summary: Refactoring Priority

| Priority | Item | Lines Removed | Risk Eliminated |
|----------|------|---------------|-----------------|
| **P0** | Replace `_list_raw()` with `list()` | ~60 lines (RawArtefact, _parse_raw_artefacts, from_raw) | Eliminates root cause of VIDEO/SLIDES bug |
| **P0** | Import from public modules, not `_artifacts` | 2 import lines | Eliminates breakage on upstream refactor |
| **P1** | Replace `ArtifactTypeCode` (IntEnum) with `ArtifactType` (str enum) | ~15 lines (NAME_TO_TYPE, TYPE_TO_NAME) | Eliminates integer-code drift risk |
| **P1** | Evaluate `wait_for_completion()` vs custom polling | ~40 lines (_poll_by_type, snapshot logic) | Gets media-readiness checks for free |
| **P2** | Replace string-based quota detection with typed exceptions | ~10 lines | Reduces fragile pattern matching |
| **P2** | Adopt exponential backoff for polling | 1 line change | Faster completion detection |

### Estimated net reduction: ~100 lines of wrapper code eliminated

The wrapper would shrink from 735 to ~635 lines, with all fragile private-API and raw-array-parsing code removed. The remaining wrapper code (auth retry, generation orchestration, download orchestration, deduplication) provides genuine value-add above the upstream library.
