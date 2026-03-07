# Pipeline Architecture

> The `pipeline` command: from git repo to hosted artefacts in one shot.

## Overview

`repo-artefacts pipeline` is the all-in-one command that chains every step — collect, upload, generate, download, publish, verify, and cleanup — into a single invocation.

```bash
repo-artefacts pipeline /path/to/repo
```

## Pipeline Flow

```mermaid
graph TD
    A[repo-artefacts pipeline] --> B{notebook_id provided?}

    B -->|No| C["Step 1: Collect & Upload"]
    C --> C1[Scan repo files]
    C1 --> C2[Render to PDF]
    C2 --> C3[Upload to NotebookLM]
    C3 --> C4[Clean up temp files]

    B -->|Yes| D["Step 1: Use existing notebook"]

    C4 --> E["Step 2: Generate artefacts"]
    D --> E
    E --> E1["🎧 Audio  🎬 Video  📊 Slides  🖼️ Infographic"]

    E1 --> F["Step 3: Download artefacts"]
    F --> F1[Save to docs/artefacts/]

    F1 --> G["Step 4: Check artefacts"]
    G --> G1{All files present?}
    G1 -->|No| G2[Exit with error]
    G1 -->|Yes| H["Step 5: Setup GitHub Pages"]

    H --> H1[Create index.html player]
    H1 --> H2[Update README.md]
    H2 --> H3[Enable Pages via API]

    H3 --> I["Step 6: Commit & push"]
    I --> J["Step 7: Verify deployment"]
    J --> J1["Poll Pages URL until 200"]

    J1 --> K{keep_notebook?}
    K -->|No| L["Step 8: Delete notebook"]
    K -->|Yes| M["Done — notebook kept"]
    L --> M
```

## Sequence Diagram

```mermaid
sequenceDiagram
    actor User
    participant CLI as repo-artefacts pipeline
    participant Col as collector
    participant NLM as NotebookLM API
    participant GH as GitHub

    User->>CLI: pipeline /path/to/repo

    rect rgb(30, 40, 60)
    Note over CLI,NLM: Collect & Upload
    CLI->>Col: collect_repo_content()
    Col-->>CLI: markdown
    CLI->>Col: render_to_pdf()
    Col-->>CLI: PDF
    CLI->>NLM: upload_repo(pdf)
    NLM-->>CLI: notebook_id
    end

    rect rgb(40, 30, 60)
    Note over CLI,NLM: Generate & Download
    CLI->>NLM: generate_artefacts(all)
    loop Poll per artefact type
        CLI->>NLM: Check status
        alt Failed
            CLI->>NLM: Delete + retry (max 3)
        end
    end
    NLM-->>CLI: All complete
    CLI->>NLM: download_artefacts()
    NLM-->>CLI: audio, video, slides, infographic
    end

    rect rgb(30, 50, 40)
    Note over CLI,GH: Publish
    CLI->>CLI: Create index.html player
    CLI->>CLI: Update README.md
    CLI->>GH: Enable GitHub Pages (API)
    CLI->>GH: git commit + push
    end

    rect rgb(50, 40, 30)
    Note over CLI,GH: Verify & Cleanup
    loop Poll until 200
        CLI->>GH: HEAD request to Pages URL
    end
    GH-->>CLI: 200 OK
    CLI->>NLM: delete_notebook()
    end

    CLI-->>User: ✅ Published! URL
```

## Options

| Option | Description | Default |
|---|---|---|
| `repo_path` | Path to git repository (positional) | `.` |
| `-n, --notebook-id` | Existing notebook ID (skips upload) | — |
| `--audio` | Generate audio overview | — |
| `--video` | Generate video explainer | — |
| `--slides` | Generate slide deck | — |
| `--infographic` | Generate infographic | — |
| `--exclude` | Artefact types to skip (repeatable) | — |
| `--resume` | Only generate artefacts not yet completed | `false` |
| `-r, --remote` | Git remote to push to | `origin` |
| `-t, --timeout` | Generation timeout per artefact (seconds) | `900` |
| `--keep-notebook` | Don't delete the notebook after publishing | `false` |

Selection modes (pick one):
- **Default**: generate all four types, skipping any already completed in the notebook
- **Explicit**: `--audio --video` — only generate the named types
- **Exclude**: `--exclude infographic` — generate all except the named types
- **Resume**: `--resume` — only generate types not yet completed (useful after quota hits)

## vs `publish`

`pipeline` and `publish` overlap but serve different use cases:

| | `pipeline` | `publish` |
|---|---|---|
| Collects & uploads repo | ✅ | ❌ (needs existing notebook) |
| Generates artefacts | ✅ | ✅ (skippable) |
| Downloads artefacts | ✅ | ✅ |
| Sets up GitHub Pages | ✅ | ✅ |
| Commits & pushes | ✅ | ✅ |
| Verifies deployment | ✅ | ✅ (skippable) |
| Deletes notebook after | ✅ (default) | ❌ |
| Cleans up temp files | ✅ | ❌ |

Use `pipeline` for a fresh repo you haven't touched. Use `publish` when you already have a notebook and want more control over individual steps.

## CI Integration

The pipeline is designed to run locally. For CI, use the individual commands — see [CI & Testing](ci-and-testing.md) for the GitHub Actions workflow and `act` for local CI runs.
