# Code Map — notebooklm-repo-artefacts

> Architecture, module relationships, and data flows for the `repo-artefacts` CLI tool.

## Overview

`notebooklm-repo-artefacts` collects content from a git repository, uploads it to Google NotebookLM, generates AI-powered artefacts (audio, video, slides, infographic), and publishes them via GitHub Pages.

```mermaid
graph LR
    subgraph "Input"
        REPO[Git Repository]
    end

    subgraph "repo-artefacts CLI"
        CLI[cli.py<br/>Typer commands]
        COL[collector.py<br/>Content gathering]
        NLM[notebooklm.py<br/>API integration]
        PG[pages.py<br/>GitHub Pages setup]
        PUB[publish.py<br/>E2E workflow]
    end

    subgraph "External Services"
        NLMAPI[Google NotebookLM]
        GHAPI[GitHub API]
        GHPAGES[GitHub Pages]
    end

    subgraph "Output"
        AUDIO[audio_overview.mp3]
        VIDEO[video_overview.mp4]
        SLIDES[slides.pdf]
        INFOG[infographic.png]
        HTML[index.html<br/>Player page]
    end

    REPO --> COL
    COL --> NLM
    NLM --> NLMAPI
    NLMAPI --> NLM
    NLM --> AUDIO & VIDEO & SLIDES & INFOG
    PG --> GHAPI
    PG --> HTML
    PUB --> COL & NLM & PG
    CLI --> COL & NLM & PG & PUB
```

## Module Breakdown

### cli.py — Command Router

Entry point for all CLI commands. Uses [Typer](https://typer.tiangolo.com/) for argument parsing and [Rich](https://rich.readthedocs.io/) for terminal output.

| Command | Description | Calls |
|---------|-------------|-------|
| `process` | Collect repo content → upload to NotebookLM | `collector` → `notebooklm` |
| `generate` | Generate artefacts from a notebook | `notebooklm` |
| `download` | Download artefacts to local disk | `notebooklm` |
| `list` | List notebooks or sources | `notebooklm` |
| `delete` | Delete a notebook | `notebooklm` |
| `pages` | Set up GitHub Pages player | `pages` |
| `publish` | Generate → pages → push → verify | `notebooklm` → `pages` → `publish` |
| `pipeline` | Full E2E: upload → generate → download → pages → push → verify → cleanup | `collector` → `notebooklm` → `pages` → `publish` |

### collector.py — Repository Content Gathering

Walks a git repository and assembles key files into a single markdown document for NotebookLM upload.

```mermaid
graph TD
    A[Repository Root] --> B{Find README}
    B --> C[Add README]
    A --> D{Scan docs/}
    D --> E[Add .md/.rst files]
    A --> F{Find config}
    F --> G[Add pyproject.toml etc.]
    A --> H{Scan source files}
    H --> I{Within 500KB budget?}
    I -->|Yes| J[Add source file]
    I -->|No| K[Stop — budget exceeded]
    C & E & G & J --> L[Combined Markdown]
    L --> M[render_to_pdf]
    M --> N[PDF for upload]
```

**Key constraints:**
- Total output capped at 500KB (`MAX_TOTAL_BYTES`)
- Source files capped at 500 lines each (`MAX_SOURCE_LINES`)
- Skips `.git`, `node_modules`, `__pycache__`, `.venv`, etc.
- Priority order: README → docs → config → source

### notebooklm.py — NotebookLM API Integration

Manages the full lifecycle: upload content, generate artefacts, poll for completion, download results.

```mermaid
sequenceDiagram
    participant CLI
    participant NLM as notebooklm.py
    participant API as NotebookLM API

    CLI->>NLM: generate_artefacts(notebook_id, types)
    loop For each artefact type
        NLM->>API: generate_audio/video/slides/infographic
        API-->>NLM: GenerationStatus(task_id)
    end

    loop Poll every 30s until timeout
        NLM->>API: poll_status(task_id)
        API-->>NLM: GenerationStatus
        alt is_complete
            NLM->>CLI: ✓ Ready
        else is_failed
            alt retries < MAX_RETRIES
                NLM->>API: Re-request generation
            else
                NLM->>CLI: ✗ Failed after retries
            end
        else in_progress
            NLM->>CLI: … still generating
        end
    end
```

**Retry logic:** Up to 3 retries per artefact. Handles both immediate failures (empty `task_id` from API) and failures detected during polling.

### pages.py — GitHub Pages Setup

Creates the player page, updates README links, and enables GitHub Pages via API.

```mermaid
graph TD
    A[setup_pages] --> B[Write index.html<br/>from template]
    A --> C[Update README.md<br/>Generated Artefacts block]
    A --> D[enable_github_pages]
    D --> E{Get GITHUB_TOKEN}
    E --> F[env var]
    E --> G[tokens.age]
    E --> H[macOS Keychain]
    E --> I[1Password CLI]
    F & G & H & I --> J{Token found?}
    J -->|Yes| K[POST /repos/.../pages]
    J -->|No| L[Skip — manual setup]
```

**Token resolution chain** (first match wins):
1. `GITHUB_TOKEN` environment variable
2. `~/.config/secrets/tokens.age` (age-encrypted, decrypted with `~/.config/age/keys.txt`)
3. macOS Keychain (`api-keys` service)
4. 1Password CLI (`op` — API_KEYS vault)

### publish.py — End-to-End Workflow

Orchestrates the full pipeline: generate → check → pages → push → verify.

```mermaid
graph TD
    A[publish command] --> B{skip_generate?}
    B -->|No| C[Generate artefacts<br/>via NotebookLM]
    C --> D[Download artefacts]
    B -->|Yes| E[Check existing files]
    D --> E
    E --> F{Standard files exist?}
    F -->|No| G[Exit with error]
    F -->|Yes| H[Setup GitHub Pages]
    H --> I[Git commit + push]
    I --> J{skip_verify?}
    J -->|No| K[Poll Pages URL<br/>until 200 or timeout]
    J -->|Yes| L[Done]
    K --> L
```

## CI Pipeline

GitHub Actions runs on every push/PR to `main`. Can be run locally with [`act`](https://github.com/nektos/act).

```mermaid
graph LR
    subgraph "Triggers"
        A[Push to main]
        B[Pull Request]
        C[Manual dispatch]
    end

    subgraph "CI Jobs (Python 3.12 + 3.13)"
        D[Checkout] --> E[Setup Python + uv]
        E --> F[Install deps]
        F --> G[Pre-commit checks]
        G --> H[Pytest]
        H --> I[Build package]
    end

    A & B & C --> D
```

Pre-commit hooks: `ruff` (lint + format), `pyright` (type check), `pytest` (tests), `detect-secrets`, standard file checks.

See [CI & Testing](ci-and-testing.md) for `act` setup and local testing.

## Interfaces

| Module | Exports | Used By |
|--------|---------|---------:|
| `collector` | `collect_repo_content()`, `render_to_pdf()` | `cli.process`, `cli.pipeline` |
| `notebooklm` | `upload_repo()`, `generate_artefacts()`, `download_artefacts()`, `list_*()`, `delete_notebook()` | `cli.*`, `publish`, `pipeline` |
| `pages` | `get_github_info()`, `get_github_token()`, `setup_pages()`, `enable_github_pages()` | `cli.pages`, `cli.publish`, `cli.pipeline` |
| `publish` | `check_artefacts()`, `verify_pages()`, `git_commit_and_push()` | `cli.publish`, `cli.pipeline` |

## Dependencies

```mermaid
graph BT
    CLI[cli.py] --> COL[collector.py]
    CLI --> NLM[notebooklm.py]
    CLI --> PG[pages.py]
    CLI --> PUB[publish.py]
    PUB --> NLM
    PUB --> PG

    NLM -.-> NLMPY[notebooklm-py]
    COL -.-> MD2PDF[md2pdf-mermaid]
    CLI -.-> TYPER[typer]
    CLI -.-> RICH[rich]
```

Solid lines = internal imports. Dotted lines = external packages.
