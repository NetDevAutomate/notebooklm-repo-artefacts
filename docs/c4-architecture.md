# C4 Architecture: notebooklm-repo-artefacts

This document describes the architecture of `notebooklm-repo-artefacts` using the C4 model.
C4 is a hierarchical notation: each level zooms into the previous one, from the system
boundary down to code-level class diagrams. All diagrams use Mermaid.

---

## Contents

1. [Level 1 — System Context](#level-1--system-context)
2. [Level 2 — Container Diagram](#level-2--container-diagram)
3. [Level 3 — Component Diagrams](#level-3--component-diagrams)
   - [Pipeline Engine](#pipeline-engine-components)
   - [NotebookLM Integration](#notebooklm-integration-components)
4. [Level 4 — Code Diagrams](#level-4--code-diagrams)
   - [Exception Hierarchy](#exception-hierarchy)
   - [Pipeline Data Model](#pipeline-data-model)
   - [Stage Protocol](#stage-protocol)
   - [Config Dataclass](#config-dataclass)
5. [Sequence Diagrams](#sequence-diagrams)
   - [Full Pipeline Run](#full-pipeline-run)
   - [Artefact Generation with Retries](#artefact-generation-with-retries)
   - [Store Publishing Flow](#store-publishing-flow)
6. [Deployment Diagram](#deployment-diagram)
7. [Key Design Decisions](#key-design-decisions)

---

## Level 1 — System Context

The context diagram shows `notebooklm-repo-artefacts` as a black box and all external systems
it depends on or talks to. This is the widest view: it answers "what does the system touch?"

```mermaid
C4Context
  title System Context: notebooklm-repo-artefacts

  Person(dev, "Developer", "Runs the CLI from their local machine to generate and publish AI artefacts for a git repository")

  System(tool, "notebooklm-repo-artefacts", "Python CLI tool. Reads a git repository, generates AI artefacts (audio, video, slides, infographic) via Google NotebookLM, then publishes them.")

  System_Ext(notebooklm, "Google NotebookLM", "AI platform. Accepts uploaded documents, generates audio overviews, video explainers, slide decks, and infographics.")

  System_Ext(github_api, "GitHub API", "REST API used to resolve the GitHub token, detect Pages configuration, and enable GitHub Pages on a repository.")

  System_Ext(github_pages, "GitHub Pages", "Static site hosting on github.io or a custom CNAME domain. Serves the artefact player HTML and binary artefact files.")

  System_Ext(git_repo, "Source Git Repository", "The repository being documented. Provides README, docs, config files, and source code for collection.")

  System_Ext(artefact_store, "Artefact Store Repository", "Optional separate GitHub repository that aggregates artefacts from many source repos. Has its own GitHub Pages site with a manifest.json index.")

  Rel(dev, tool, "Invokes", "CLI: repo-artefacts pipeline / publish / migrate / validate")
  Rel(tool, git_repo, "Reads files from", "Local filesystem + git commands")
  Rel(tool, notebooklm, "Uploads PDF, triggers generation, polls status, downloads files", "HTTPS via notebooklm-py library (Playwright-authenticated browser session)")
  Rel(tool, github_api, "Reads remote URL, enables Pages, resolves token", "HTTPS REST API")
  Rel(tool, github_pages, "Pushes index.html + artefacts, verifies deployment", "git push + HTTP HEAD polling")
  Rel(tool, artefact_store, "Clones, publishes artefacts, commits, pushes", "git clone/push over SSH or HTTPS")
  Rel(dev, github_pages, "Views hosted artefacts", "HTTPS browser")
```

### External System Notes

| External System | Protocol | Auth |
|---|---|---|
| Google NotebookLM | Browser session via `notebooklm-py[browser]` (Playwright) | Google account cookie/CSRF stored by `NotebookLMClient.from_storage()` |
| GitHub API | HTTPS REST | `GITHUB_TOKEN` resolved from: env var, age-encrypted file, macOS Keychain, or 1Password CLI |
| GitHub Pages | HTTP (polling) | Public — no auth needed for verification |
| Source git repo | Local filesystem | None — the developer's working directory |
| Artefact store | git over SSH or HTTPS | Same `GITHUB_TOKEN` as above; SSH key or token-in-URL |

---

## Level 2 — Container Diagram

A container is a separately deployable or runnable unit. For this CLI tool everything runs in
one Python process, so containers correspond to the major source modules — each with a distinct
technical responsibility.

```mermaid
C4Container
  title Container Diagram: notebooklm-repo-artefacts

  Person(dev, "Developer", "")

  System_Boundary(tool, "notebooklm-repo-artefacts") {

    Container(cli, "CLI Application", "Python / Typer", "Entry point. Parses arguments, wires up containers, delegates to pipeline or individual commands. src/repo_artefacts/cli.py")

    Container(pipeline, "Pipeline Engine", "Python / asyncio", "Stage-based orchestrator. Runs 9 ordered stages with pre_check → execute → post_check gates. Persists state to .pipeline-state.json for resumability. src/repo_artefacts/pipeline.py")

    Container(collector, "Content Collector", "Python / md2pdf-mermaid / Playwright", "Walks the source repo, applies priority-based collection rules, assembles a single markdown document, then renders it to PDF via Playwright/Chromium. src/repo_artefacts/collector.py")

    Container(notebooklm_int, "NotebookLM Integration", "Python / notebooklm-py / asyncio", "Creates and manages NotebookLM notebooks. Uploads PDF, polls source readiness, concurrently generates all 4 artefact types with retry/reauth logic, downloads completed files. src/repo_artefacts/notebooklm.py")

    Container(publisher, "Publishing Engine", "Python / subprocess", "Checks which artefact files are present locally, generates the HTML player page, injects artefact links into README.md, commits and pushes, verifies GitHub Pages deployment. src/repo_artefacts/publish.py + pages.py")

    Container(store_mgr, "Store Manager", "Python / git subprocess", "Manages a separate centralised artefact-store repository. Shallow-clones or pulls it, copies artefact files, generates a manifest.json index, commits and pushes. src/repo_artefacts/store.py")

    Container(config, "Configuration", "Python / TOML", "Loads ~/.config/repo-artefacts/config.toml. Provides defaults for store slug, generation timeout, and store cache directory. src/repo_artefacts/config.py")
  }

  System_Ext(notebooklm, "Google NotebookLM", "")
  System_Ext(github_api, "GitHub API", "")
  System_Ext(github_pages, "GitHub Pages", "")
  System_Ext(git_repo, "Source Git Repository", "")
  System_Ext(artefact_store, "Artefact Store Repo", "")

  Rel(dev, cli, "Runs", "CLI commands")
  Rel(cli, pipeline, "Invokes", "run_pipeline()")
  Rel(cli, config, "Reads", "load_config()")
  Rel(pipeline, collector, "Stage: CollectStage", "collect_repo_content(), render_to_pdf()")
  Rel(pipeline, notebooklm_int, "Stages: UploadStage, GenerateStage, DownloadStage, CleanupStage", "upload_repo(), generate_artefacts(), download_artefacts(), delete_notebook()")
  Rel(pipeline, publisher, "Stages: LocalPublishStage, LocalVerifyStage", "setup_pages(), git_commit_and_push(), verify_pages()")
  Rel(pipeline, store_mgr, "Stages: PublishStage, VerifyStage, ReadmeStage", "clone_or_pull_store(), publish_to_store(), commit_and_push_store()")
  Rel(collector, git_repo, "Reads", "Local filesystem glob + git commands")
  Rel(notebooklm_int, notebooklm, "API calls", "HTTPS via notebooklm-py")
  Rel(publisher, github_api, "Enables Pages, reads remote", "HTTPS REST")
  Rel(publisher, github_pages, "Pushes artefacts, verifies", "git + HTTP polling")
  Rel(store_mgr, artefact_store, "Clone / push", "git")
```

### Container Responsibilities

| Container | Source File(s) | Key Dependency |
|---|---|---|
| CLI Application | `cli.py` | `typer`, `rich` |
| Pipeline Engine | `pipeline.py` | All other containers |
| Content Collector | `collector.py` | `md2pdf-mermaid`, Playwright |
| NotebookLM Integration | `notebooklm.py` | `notebooklm-py[browser]` |
| Publishing Engine | `publish.py`, `pages.py` | `subprocess` (git), `urllib` |
| Store Manager | `store.py` | `subprocess` (git) |
| Configuration | `config.py` | `tomllib` (stdlib) |

---

## Level 3 — Component Diagrams

Level 3 zooms into the two most complex containers: the Pipeline Engine and the NotebookLM
Integration. These contain enough internal structure to warrant their own component view.

### Pipeline Engine Components

The pipeline is a linear sequence of 9 stages. Each stage is a class with three methods.
The runner (`run_pipeline`) drives state transitions and persists results after every stage
so a failed run can be resumed.

```mermaid
C4Component
  title Component Diagram: Pipeline Engine (pipeline.py)

  Container_Boundary(pipeline, "Pipeline Engine") {

    Component(state, "PipelineState", "dataclass", "Persisted pipeline state: notebook_id, content_hash, per-stage outcomes, artefact statuses. Serialised to .pipeline-state.json. Loaded on --resume.")

    Component(ctx, "PipelineContext", "dataclass", "Shared context passed to every stage. Holds repo_path, store_slug, output_dir, flags (keep_notebook, force_regen, dry_run), timeout, and references to PipelineState and the state file path.")

    Component(result_types, "StageResult / Status", "dataclass + StrEnum", "StageResult carries status (PASS/FAIL/SKIP/RETRY), message, and a data dict. Status is a StrEnum so values serialise cleanly to JSON.")

    Component(runner, "run_pipeline()", "function", "The outer loop. Iterates ALL_STAGES. On --resume, skips stages with status='pass'. Calls pre_check → execute → post_check for each stage. Breaks on first FAIL. Persists state after each stage. Sends macOS notification on completion.")

    Component(collect_stage, "CollectStage", "class", "pre_check: repo path and .git dir exist. execute: calls collect_repo_content() then render_to_pdf(), stores SHA256 hash of PDF. post_check: PDF exists and non-empty.")

    Component(upload_stage, "UploadStage", "class", "pre_check: PDF exists; skips if content hash unchanged from previous upload (deduplication on resume). execute: calls upload_repo(), stores notebook_id. post_check: notebook_id is set.")

    Component(generate_stage, "GenerateStage", "class", "pre_check: notebook_id exists. execute: resolves artefact target list, calls generate_artefacts(), records completed/failed/quota_exhausted sets in state. post_check: all targeted artefacts show 'completed'.")

    Component(download_stage, "DownloadStage", "class", "pre_check: notebook_id exists and at least one artefact completed. execute: calls download_artefacts(). post_check: calls check_artefacts() to confirm files on disk.")

    Component(publish_stage, "PublishStage", "class", "pre_check: store_slug set and valid (SKIP if no store). execute: clone_or_pull_store, publish_to_store, commit_and_push_store.")

    Component(local_publish_stage, "LocalPublishStage", "class", "pre_check: SKIP if store mode. execute: setup_pages (writes index.html, updates README), git_commit_and_push.")

    Component(verify_stage, "VerifyStage", "class", "pre_check: SKIP if no store. execute: resolves base URL from CNAME or default pattern, calls verify_pages() with artefact URLs.")

    Component(local_verify_stage, "LocalVerifyStage", "class", "pre_check: SKIP if store mode. execute: constructs {org}.github.io URL, calls verify_pages().")

    Component(cleanup_stage, "CleanupStage", "class", "pre_check: SKIP if keep_notebook flag or all artefacts not done. execute: delete_notebook(). Acceptable artefact states are 'completed' OR 'quota_exhausted'.")
  }

  Rel(runner, state, "Loads/saves after each stage", "PipelineState.load() / .save()")
  Rel(runner, ctx, "Creates and passes to all stages", "PipelineContext")
  Rel(runner, collect_stage, "Executes stage", "pre_check → execute → post_check")
  Rel(runner, upload_stage, "Executes stage", "")
  Rel(runner, generate_stage, "Executes stage", "")
  Rel(runner, download_stage, "Executes stage", "")
  Rel(runner, publish_stage, "Executes stage", "")
  Rel(runner, local_publish_stage, "Executes stage", "")
  Rel(runner, verify_stage, "Executes stage", "")
  Rel(runner, local_verify_stage, "Executes stage", "")
  Rel(runner, cleanup_stage, "Executes stage", "")
  Rel(collect_stage, ctx, "Writes pdf_path, md_path, content_hash", "")
  Rel(upload_stage, ctx, "Writes notebook_id, source_replaced", "")
  Rel(generate_stage, ctx, "Writes artefacts dict", "")
```

#### Stage Execution Protocol

Every stage class implements this implicit protocol. The runner enforces the sequence.

| Method | PASS means | SKIP means | FAIL means |
|---|---|---|---|
| `pre_check(ctx)` | Proceed to execute | Skip this stage entirely (not an error) | Abort pipeline |
| `execute(ctx)` | Stage work complete | N/A | Abort pipeline |
| `post_check(ctx)` | Stage verified | N/A | Abort pipeline |

#### Stage Ordering and Mutual Exclusivity

Stages 5–8 are mutually exclusive by design: `PublishStage` and `VerifyStage` activate
only when `store_slug` is set; `LocalPublishStage` and `LocalVerifyStage` activate only
when `store_slug` is absent. The runner runs all 9 stages unconditionally — the
`pre_check` SKIP mechanism handles the conditional logic.

```mermaid
flowchart TD
    A[CollectStage] --> B[UploadStage]
    B --> C[GenerateStage]
    C --> D[DownloadStage]
    D --> E{store_slug set?}
    E -- yes --> F[PublishStage]
    E -- no  --> G[LocalPublishStage]
    F --> H[VerifyStage]
    G --> I[LocalVerifyStage]
    H --> J[ReadmeStage]
    I --> J
    J --> K[CleanupStage]

    style E fill:#f5f5dc,stroke:#999
```

---

### NotebookLM Integration Components

This container encapsulates all communication with the NotebookLM API. It is the most
complex part of the codebase because the external API is asynchronous, requires browser-based
auth, has per-type quota limits, and returns inconsistent error codes.

```mermaid
C4Component
  title Component Diagram: NotebookLM Integration (notebooklm.py)

  Container_Boundary(nb, "NotebookLM Integration") {

    Component(reauth, "_with_reauth()", "async function", "Retry wrapper for any NotebookLM API call. Handles AuthError (stale CSRF: refresh + retry), RateLimitError (RATE_LIMIT_BACKOFF + refresh), and RPCError (refresh + retry). Backoff schedule: [2, 10, 30]s for auth; [5, 15, 30, 60, 120]s for rate limits.")

    Component(artefact_config, "ARTEFACT_CONFIG", "dict", "Configuration for each of the 4 artefact types. Maps type name → instructions string + API method name. Drives both generation dispatch and download.")

    Component(upload_repo, "upload_repo()", "async function", "Creates or reuses a NotebookLM notebook. On fresh run: deletes any existing notebook with same title to avoid stale duplicates. Uploads PDF source. Polls source readiness (is_ready) for up to 120s at 5s intervals before returning.")

    Component(generate_artefacts, "generate_artefacts()", "async function", "Top-level generation orchestrator. Checks already-completed types, submits up to CONCURRENCY_LIMIT=2 requests via asyncio.Semaphore, polls with POLL_WINDOW=60s windows, retries failed items with shared exponential backoff. Returns GenerateResult.")

    Component(gen_result, "GenerateResult", "dataclass", "Output of generate_artefacts(). Three sets: completed, failed, quota_exhausted. Returned to GenerateStage for pipeline state update.")

    Component(request_artefact, "_request_artefact()", "async function", "Fires a single generation request. Dispatches to the correct client.artifacts method (generate_audio, generate_video, generate_slide_deck, generate_infographic) with type-specific kwargs from _GENERATE_KWARGS.")

    Component(wait_artefact, "_wait_for_artefact()", "async function", "Polls client.artifacts.get() with exponential backoff (2s → 10s max). Each poll yields to the event loop so concurrent artefacts make progress. Returns Artifact when is_completed or is_failed, or on timeout.")

    Component(quota_check, "_is_quota_error()", "function", "Inspects GenerationStatus.error and error_code fields. Returns True if error_code == 'USER_DISPLAYABLE_ERROR' or error message matches QUOTA_ERROR_PATTERNS. Prevents quota-exhausted artefacts from consuming retry budget.")

    Component(download_artefacts, "download_artefacts()", "async function", "Downloads all completed artefacts to output_dir. Data-driven via _DOWNLOAD_SPECS: [(type, list_method, download_method, filename), ...]. Skips artefacts that exist but are not is_completed.")

    Component(deduplicate, "_deduplicate_sources()", "async function", "Pre-generation guard. Lists all sources in the notebook and removes duplicates (same title), keeping only the most recently added. Prevents confused generation from stale source data.")
  }

  System_Ext(notebooklm_api, "Google NotebookLM API", "")

  Rel(generate_artefacts, reauth, "Wraps all API calls with", "")
  Rel(generate_artefacts, request_artefact, "Submits generation requests via", "asyncio.gather + semaphore")
  Rel(generate_artefacts, wait_artefact, "Polls pending artefacts via", "asyncio.gather")
  Rel(generate_artefacts, quota_check, "Classifies failures using", "")
  Rel(generate_artefacts, deduplicate, "Pre-flight deduplication via", "")
  Rel(generate_artefacts, gen_result, "Returns", "")
  Rel(upload_repo, reauth, "Wraps all API calls with", "")
  Rel(request_artefact, artefact_config, "Reads method name and instructions from", "")
  Rel(download_artefacts, reauth, "Wraps all download calls with", "")
  Rel(reauth, notebooklm_api, "Makes calls to", "HTTPS via notebooklm-py")
```

#### Concurrency Model

```mermaid
sequenceDiagram
    participant G as generate_artefacts()
    participant S as asyncio.Semaphore(2)
    participant A1 as _submit_one(audio)
    participant A2 as _submit_one(video)
    participant A3 as _submit_one(slides)
    participant A4 as _submit_one(infographic)
    participant P as _poll_one() × N

    G->>S: gather 4 _submit_one coroutines
    S-->>A1: acquire (slot 1)
    S-->>A2: acquire (slot 2)
    Note over A3,A4: blocked — semaphore full
    A1-->>S: release (audio submitted)
    S-->>A3: acquire (slot 1)
    A2-->>S: release (video submitted)
    S-->>A4: acquire (slot 2)
    A3-->>S: release (slides submitted)
    A4-->>S: release (infographic submitted)

    G->>P: gather poll coroutines (POLL_WINDOW=60s each)
    P-->>G: Artifact results (completed/failed/in_progress)
    G->>G: retry loop for failed items
```

---

## Level 4 — Code Diagrams

Class-level detail for the key types in the system. These match the actual source
and are useful as a reference when reading or extending the code.

### Exception Hierarchy

`src/repo_artefacts/exceptions.py` and `src/repo_artefacts/store.py`

```mermaid
classDiagram
    class Exception {
        <<Python built-in>>
    }

    class RepoArtefactsError {
        +str message
        "Base exception for all library errors.
        Catch this to handle any domain error
        in one except clause."
    }

    class GitRemoteError {
        "Could not determine GitHub org/repo
        from git remote."
    }

    class CollectionError {
        "Failed to collect repository content.
        Raised by collector.py."
    }

    class StoreError {
        "Error during artefact store operations.
        Raised by store.py."
    }

    Exception <|-- RepoArtefactsError
    RepoArtefactsError <|-- GitRemoteError
    RepoArtefactsError <|-- CollectionError
    RepoArtefactsError <|-- StoreError
```

The CLI's `_handle_errors` decorator catches `RepoArtefactsError` and translates it to
`typer.Exit(code=1)`. Library callers can catch the base class for a single handler.

---

### Pipeline Data Model

`src/repo_artefacts/pipeline.py`

```mermaid
classDiagram
    class Status {
        <<StrEnum>>
        PASS = "pass"
        FAIL = "fail"
        SKIP = "skip"
        RETRY = "retry"
    }

    class StageResult {
        +Status status
        +str message
        +dict~str, Any~ data
    }

    class PipelineState {
        +str repo_name
        +str notebook_id
        +str content_hash
        +bool source_replaced
        +dict~str, dict~ stages
        +dict~str, str~ artefacts
        +str started_at
        +str updated_at
        +save(path: Path) None
        +load(path: Path) PipelineState$
        +stage_status(name: str) str
        +set_stage(name: str, status: str, **extra) None
    }

    class PipelineContext {
        +Path repo_path
        +str|None store_slug
        +Path output_dir
        +bool keep_notebook
        +bool force_regen
        +bool dry_run
        +int timeout
        +PipelineState state
        +Path state_path
        +list~str~|None artefact_selection
        +Path|None pdf_path
        +Path|None md_path
        +save_state() None
    }

    PipelineContext "1" --> "1" PipelineState : holds reference to
    StageResult "1" --> "1" Status : status field
```

**State persistence**: `PipelineState.save()` writes JSON to `docs/artefacts/.pipeline-state.json`
after every stage. `PipelineState.load()` reads it on `--resume`. The `stages` dict records
the outcome of each stage by name, plus any extra data (e.g., `notebook_id`, `content_hash`,
`duration_s`) that may be useful for debugging.

**Artefact status values stored in `PipelineState.artefacts`**: `"completed"`, `"failed"`,
`"quota_exhausted"`. The `CleanupStage` treats both `"completed"` and `"quota_exhausted"`
as acceptable (the notebook is still deleted so credits are not wasted).

---

### Stage Protocol

All stage classes implement this implicit structural protocol. Python does not enforce it
with an ABC; the runner calls the methods by name.

```mermaid
classDiagram
    class StageProtocol {
        <<implicit interface>>
        +str name
        +pre_check(ctx: PipelineContext) StageResult
        +execute(ctx: PipelineContext) StageResult
        +post_check(ctx: PipelineContext) StageResult
    }

    class CollectStage {
        +name = "collect"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class UploadStage {
        +name = "upload"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class GenerateStage {
        +name = "generate"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class DownloadStage {
        +name = "download"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class PublishStage {
        +name = "publish"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class LocalPublishStage {
        +name = "local_publish"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class VerifyStage {
        +name = "verify"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class LocalVerifyStage {
        +name = "local_verify"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class ReadmeStage {
        +name = "readme"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    class CleanupStage {
        +name = "cleanup"
        +pre_check(ctx) StageResult
        +execute(ctx) StageResult
        +post_check(ctx) StageResult
    }

    StageProtocol <|.. CollectStage
    StageProtocol <|.. UploadStage
    StageProtocol <|.. GenerateStage
    StageProtocol <|.. DownloadStage
    StageProtocol <|.. PublishStage
    StageProtocol <|.. LocalPublishStage
    StageProtocol <|.. VerifyStage
    StageProtocol <|.. LocalVerifyStage
    StageProtocol <|.. ReadmeStage
    StageProtocol <|.. CleanupStage
```

`ALL_STAGES` in `pipeline.py` is the single ordered list of instantiated stage objects that
`run_pipeline()` iterates. Adding a new stage means appending to that list.

---

### Config Dataclass

`src/repo_artefacts/config.py` — loaded by `cli.py` and `store.py`

```mermaid
classDiagram
    class Config {
        +str|None default_store
        "GitHub org/repo slug for the artefact store.
        e.g. 'MyOrg/artefact-store'"
        +int default_timeout = 900
        "Generation timeout in seconds (default 15 min)."
        +Path store_cache_dir
        "Local clone cache: ~/.cache/repo-artefacts/stores/"
    }

    class load_config {
        <<function>>
        +Config load_config()
        "Reads ~/.config/repo-artefacts/config.toml.
        Returns default Config if file absent or invalid."
    }

    class save_config {
        <<function>>
        +None save_config(config: Config)
        "Writes config to TOML. Creates dir if needed."
    }

    load_config ..> Config : returns
    save_config ..> Config : reads
```

**Config file location**: `~/.config/repo-artefacts/config.toml`

Example:
```toml
default_store = "MyOrg/artefact-store"
default_timeout = 1200
```

---

## Sequence Diagrams

### Full Pipeline Run

This shows the happy-path flow for `repo-artefacts pipeline /path/to/repo --store org/store`.
Error handling and SKIP paths are omitted for readability.

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant CLI as cli.py
    participant PE as pipeline.py
    participant COL as collector.py
    participant NB as notebooklm.py
    participant ST as store.py
    participant PUB as publish.py / pages.py
    participant FS as Local Filesystem
    participant NLM as Google NotebookLM
    participant GH as GitHub / Pages

    Dev->>CLI: repo-artefacts pipeline /path/to/repo --store org/store
    CLI->>CLI: load_config(), resolve artefact selection
    CLI->>PE: run_pipeline(repo_path, store_slug="org/store")

    note over PE: Stage: collect
    PE->>COL: collect_repo_content(repo_path, md_path)
    COL->>FS: glob + read files (priority rules)
    COL-->>PE: md_path written
    PE->>COL: render_to_pdf(md_path)
    COL->>FS: Playwright/Chromium render
    COL-->>PE: pdf_path, content_hash

    note over PE: Stage: upload
    PE->>NB: upload_repo(pdf_path, repo_name)
    NB->>NLM: notebooks.list() → delete old → notebooks.create()
    NB->>NLM: sources.add_file(notebook_id, pdf_path)
    loop poll source readiness (5s intervals, max 120s)
        NB->>NLM: sources.list(notebook_id)
        NLM-->>NB: source.is_ready
    end
    NB-->>PE: {id, title, source_replaced}

    note over PE: Stage: generate
    PE->>NB: generate_artefacts(notebook_id, ["audio","video","slides","infographic"])
    NB->>NLM: artifacts.generate_audio() + artifacts.generate_video() [semaphore: 2]
    NB->>NLM: artifacts.generate_slide_deck() + artifacts.generate_infographic()
    loop poll until completed/failed/timeout (60s windows)
        NB->>NLM: artifacts.get(notebook_id, task_id) × N
    end
    NB-->>PE: GenerateResult(completed={all 4})

    note over PE: Stage: download
    PE->>NB: download_artefacts(notebook_id, output_dir)
    NB->>NLM: artifacts.list_audio/video/slide_decks/infographics()
    NB->>FS: download files → docs/artefacts/
    NB-->>PE: done

    note over PE: Stage: publish (store mode)
    PE->>ST: clone_or_pull_store("org/store")
    ST->>GH: git clone --depth 1 (or pull)
    ST-->>PE: store_path
    PE->>ST: publish_to_store(store_path, repo_name, output_dir)
    ST->>FS: copy artefact files → store/repo_name/artefacts/
    ST->>FS: write index.html player, update manifest.json
    PE->>ST: commit_and_push_store(store_path, repo_name)
    ST->>GH: git commit + push

    note over PE: Stage: local_publish (SKIPPED — store mode)

    note over PE: Stage: verify (store mode)
    PE->>PUB: verify_pages(base_url, artefact_urls)
    loop HTTP HEAD polling (10s intervals, max 120s)
        PUB->>GH: HEAD store/repo/artefacts/
        GH-->>PUB: 200 OK + Last-Modified header
    end
    PUB-->>PE: (site_ok=True, verified={"audio","video","slides","infographic"})

    note over PE: Stage: readme
    PE->>PUB: setup_pages(repo_path, org, repo, store_base_url)
    PUB->>FS: update README.md artefacts block
    PE->>PUB: git_commit_and_push(repo_path, "docs: update artefact links")
    PUB->>GH: git push origin main

    note over PE: Stage: cleanup
    PE->>NB: delete_notebook(notebook_id)
    NB->>NLM: notebooks.delete(notebook_id)

    PE-->>CLI: True (all_passed)
    CLI-->>Dev: exit 0
```

---

### Artefact Generation with Retries

This focuses on the retry and quota detection logic inside `generate_artefacts()`.

```mermaid
sequenceDiagram
    participant GA as generate_artefacts()
    participant SEM as asyncio.Semaphore(2)
    participant API as NotebookLM API
    participant RA as _with_reauth()

    GA->>GA: _deduplicate_sources() — remove duplicate uploads
    GA->>API: artifacts.list() — check already completed

    par Submit phase (max 2 concurrent via semaphore)
        GA->>SEM: acquire
        GA->>RA: _delete_existing_by_type(failed_only=True)
        RA->>API: artifacts.list() + artifacts.delete()
        GA->>RA: _request_artefact("audio")
        RA->>API: artifacts.generate_audio(notebook_id, ...)
        API-->>RA: GenerationStatus(task_id="...", status="pending")
        GA->>SEM: release
    and
        GA->>SEM: acquire
        GA->>RA: _request_artefact("video")
        RA->>API: artifacts.generate_video(notebook_id, ...)
        API-->>RA: GenerationStatus(task_id="...", status="pending")
        GA->>SEM: release
    end

    loop Poll cycle (POLL_WINDOW=60s)
        GA->>RA: _wait_for_artefact("audio", task_id, 60s)
        RA->>API: artifacts.get(notebook_id, task_id) — polls at 2s→10s interval
        API-->>RA: Artifact(is_completed=True)
        RA-->>GA: completed
        GA->>RA: _wait_for_artefact("video", task_id, 60s)
        API-->>RA: Artifact(is_failed=True)
        RA-->>GA: failed
    end

    note over GA: video failed — enter retry path
    GA->>GA: compute backoff = RATE_LIMIT_BACKOFF[retry_count]
    GA->>GA: asyncio.sleep(backoff)
    GA->>RA: client.refresh_auth()
    GA->>RA: _delete_existing_by_type("video", failed_only=True)
    GA->>RA: _request_artefact("video") — retry submit
    API-->>RA: GenerationStatus(task_id="new_id")

    note over GA: Check for quota exhaustion
    alt API returns is_failed=True AND error_code=USER_DISPLAYABLE_ERROR
        GA->>RA: client.refresh_auth()
        GA->>RA: _request_artefact("infographic") — confirm quota
        API-->>RA: is_failed=True again
        GA->>GA: quota_exhausted.add("infographic")
        GA->>GA: do NOT retry further
    end

    GA-->>GA: GenerateResult(completed={"audio","slides"}, failed={}, quota_exhausted={"infographic"})
```

---

### Store Publishing Flow

The store mode separates artefact storage from source repos — binary files live in a dedicated
store repo with its own GitHub Pages site, not in every source repo's git history.

```mermaid
sequenceDiagram
    actor Dev as Developer
    participant CLI as cli.py
    participant ST as store.py
    participant PUB as publish.py / pages.py
    participant SRC as Source Repo (local)
    participant STORE as Artefact Store Repo (GitHub)
    participant PAGES as Store GitHub Pages

    Dev->>CLI: repo-artefacts migrate /path/to/repo --store org/store

    CLI->>ST: clone_or_pull_store("org/store", token)
    ST->>STORE: git clone --depth 1 (SSH or HTTPS+token)
    ST-->>CLI: store_path (~/.cache/repo-artefacts/stores/org/store)

    CLI->>ST: publish_to_store(store_path, repo_name, artefacts_dir)
    ST->>ST: check_artefacts() — find audio/video/slides/infographic files
    ST->>STORE: copy files → store/repo_name/artefacts/
    ST->>STORE: write store/repo_name/artefacts/index.html (player page)
    ST->>STORE: update manifest.json (upsert entry with name, artefacts list, date)
    ST-->>CLI: base_url = "https://domain/{repo_name}/artefacts/"

    CLI->>ST: commit_and_push_store(store_path, repo_name)
    ST->>STORE: git add repo_name/artefacts/ manifest.json
    ST->>STORE: git commit -m "artefacts: update {repo_name}"
    alt Push conflict
        ST->>STORE: git pull --rebase
        ST->>STORE: git push (retry)
    else Clean push
        ST->>STORE: git push
    end

    STORE->>PAGES: GitHub Actions deploys Pages from main

    CLI->>PUB: setup_pages(repo_root, org, repo, store_base_url)
    PUB->>SRC: inject <!-- ARTEFACTS:START --> block into README.md
    note over PUB: Links point to store URL, not source repo

    CLI->>PUB: git_commit_and_push(repo_root, "docs: update artefact links")
    note over PUB: Only stages README.md — never git add -A

    CLI->>PUB: verify_pages(base_url, artefact_urls)
    loop HTTP HEAD poll (10s, max 120s)
        PUB->>PAGES: HEAD {base_url} and each artefact URL
        PAGES-->>PUB: 200 OK + Last-Modified
    end
    PUB-->>CLI: (site_ok=True, verified={"audio", "video", ...})
```

---

## Deployment Diagram

This shows how artefacts flow from the developer's machine through NotebookLM to their
final hosting destination. Two deployment paths exist: local mode and store mode.

```mermaid
flowchart TB
    subgraph dev["Developer Machine"]
        cli["repo-artefacts CLI"]
        src_repo["Source Git Repo\n(local checkout)"]
        pdf["Rendered PDF\ndocs/artefacts/*.pdf"]
        local_artefacts["Downloaded Artefacts\ndocs/artefacts/\naudio_overview.mp3\nvideo_overview.mp4\nslides.pdf\ninfographic.png"]
        state[".pipeline-state.json\n(resume checkpoint)"]
        store_cache["~/.cache/repo-artefacts/stores/\norg/store (shallow clone)"]
    end

    subgraph nlm["Google NotebookLM (Cloud)"]
        notebook["Notebook\n(PDF source)"]
        gen_audio["Audio Overview\n(generation job)"]
        gen_video["Video Overview\n(generation job)"]
        gen_slides["Slide Deck\n(generation job)"]
        gen_info["Infographic\n(generation job)"]
    end

    subgraph gh["GitHub"]
        src_repo_gh["Source Repo\ngithub.com/org/repo"]
        store_repo["Artefact Store\ngithub.com/org/store"]
    end

    subgraph pages["GitHub Pages (Hosting)"]
        local_pages["Source Repo Pages\nhttps://org.github.io/repo/artefacts/"]
        store_pages["Store Pages\nhttps://domain/repo_name/artefacts/"]
    end

    cli --> src_repo
    src_repo --> pdf
    pdf --> notebook
    notebook --> gen_audio & gen_video & gen_slides & gen_info
    gen_audio & gen_video & gen_slides & gen_info --> local_artefacts
    cli --> state

    local_artefacts --> store_cache

    local_artefacts -- "local mode:\ngit push docs/artefacts/ + index.html" --> src_repo_gh
    src_repo_gh --> local_pages

    store_cache -- "store mode:\ngit push repo_name/artefacts/ + manifest.json" --> store_repo
    store_repo --> store_pages

    style dev fill:#e8f4fd,stroke:#3498db
    style nlm fill:#fef9e7,stroke:#f39c12
    style gh fill:#eafaf1,stroke:#27ae60
    style pages fill:#fdf2f8,stroke:#8e44ad
```

### Deployment Path Comparison

| Aspect | Local Mode | Store Mode |
|---|---|---|
| Artefact location | `docs/artefacts/` in source repo | `{repo_name}/artefacts/` in store repo |
| Player URL | `https://{org}.github.io/{repo}/artefacts/` | `https://{domain}/{repo_name}/artefacts/` |
| Binary files in source git history | Yes (increases repo size over time) | No (use `migrate` + `git filter-repo` to clean) |
| Multi-repo index | No | Yes (`manifest.json`) |
| Source repo README changes | `index.html` link | Store URL link |
| Custom domain support | Via source repo CNAME | Via store repo CNAME |

---

## Key Design Decisions

These explain the non-obvious architectural choices visible in the C4 diagrams above.

### 1. Stage-based pipeline with explicit pre/post checks

The three-method gate pattern (`pre_check → execute → post_check`) makes two things possible:
SKIP-based conditional branching (store vs. local mode uses the same stage list with different
pre_check returns), and fine-grained failure attribution (the pipeline log records which gate
failed, not just which stage). The alternative of `if store_slug:` conditionals inside a
monolithic function would make the execution path harder to test and resume.

Source: `pipeline.py` — `run_pipeline()` loop and each stage class.

### 2. State persisted to JSON after every stage

`PipelineState.save()` is called after every stage outcome, including failures.
This makes `--resume` safe: re-running with `--resume` skips `status == "pass"` stages
and re-executes from the first incomplete or failed stage. Without per-stage persistence,
a network failure during generation would require re-uploading and re-creating the notebook
from scratch. The state file also serves as a structured audit log for debugging.

Source: `pipeline.py` — `STATE_FILENAME = ".pipeline-state.json"`.

### 3. Mutual exclusivity via SKIP, not separate code paths

`PublishStage`/`VerifyStage` and `LocalPublishStage`/`LocalVerifyStage` are in the same
`ALL_STAGES` list. They self-deactivate via `pre_check` returning `Status.SKIP` based on
whether `ctx.store_slug` is set. This keeps the runner loop simple and ensures both paths
go through the same state persistence and logging infrastructure.

### 4. Content hash deduplication on upload

`UploadStage.pre_check` computes a SHA256 of the freshly rendered PDF and compares it
against the hash stored from the previous run's upload stage. If identical, upload is
skipped. This prevents NotebookLM from consuming API credits when the source repo has
not changed since the last run, which is common when iterating on publishing configuration.

### 5. Semaphore-limited concurrent generation with short poll windows

The NotebookLM API appears to reject or throttle more than 2 simultaneous generation
requests from the same account (`CONCURRENCY_LIMIT = 2`). Using `asyncio.Semaphore`
rather than sequential submission reduces total wall-clock time by roughly 2x.
Short `POLL_WINDOW = 60s` cycles allow failed artefacts to be detected and retried
without waiting for the slowest artefact (often video at 10–15 min) to finish.

Source: `notebooklm.py` — `generate_artefacts()`.

### 6. Auth retry wrapper separates retry policy from business logic

`_with_reauth()` is a generic retry wrapper that handles the three failure modes
(AuthError, RateLimitError, RPCError) independently of what operation is being retried.
Every API call is wrapped. This means business logic functions (`upload_repo`,
`generate_artefacts`, `download_artefacts`) do not contain any auth-recovery code,
and the retry policy is in one place.

### 7. Store mode separates binary artefacts from source history

Large binary files (MP3, MP4, PDF) committed directly to a source repo inflate git
clone times permanently. The store mode publishes to a separate dedicated repo, so
source repos only carry README links. The `migrate` command adds a `git filter-repo`
path to clean history for repos that previously used local mode.

### 8. Token resolution fallback chain

`get_github_token()` in `pages.py` tries four sources in order: `GITHUB_TOKEN` env var,
age-encrypted token file, macOS Keychain, 1Password CLI. This supports CI/CD (env var),
developer machines with different secret managers, and local development without env vars.
Each source is silently skipped on failure, so no single secret manager is required.
