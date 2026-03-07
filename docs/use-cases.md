# Use Cases — notebooklm-repo-artefacts

## UC1: Generate artefacts for a new repo

> You have a git repo and want audio, video, slides, and infographic overviews.

```mermaid
sequenceDiagram
    actor User
    participant CLI as repo-artefacts
    participant NLM as NotebookLM

    User->>CLI: repo-artefacts process ./my-repo
    CLI->>CLI: Collect README, docs, config, source
    CLI->>CLI: Render markdown → PDF
    CLI->>NLM: Upload PDF as notebook source
    NLM-->>CLI: notebook_id
    CLI-->>User: export NOTEBOOK_ID=abc123

    User->>CLI: repo-artefacts generate -n abc123
    CLI->>NLM: Request audio, video, slides, infographic
    loop Poll every 30s
        CLI->>NLM: Check status
    end
    NLM-->>CLI: All complete

    User->>CLI: repo-artefacts download -n abc123
    CLI->>NLM: Download all artefacts
    CLI-->>User: Files in ./docs/artefacts/
```

**Steps:**
```bash
# 1. Collect and upload
repo-artefacts process ./my-repo -n NOTEBOOK_ID
# or let it create a new notebook:
repo-artefacts process ./my-repo

# 2. Generate all artefacts
export NOTEBOOK_ID=<id from step 1>
repo-artefacts generate

# 3. Download
repo-artefacts download -o ./docs/artefacts
```

## UC2: Publish with GitHub Pages (one command)

> You have artefacts already generated and want to publish them with a player page.

```bash
# Full pipeline (skip generation, use existing files)
repo-artefacts publish ./my-repo --skip-generate --remote origin

# Full pipeline including generation
repo-artefacts publish ./my-repo -n $NOTEBOOK_ID
```

This will:
1. Check standard files exist (`audio_overview.m4a`, `video_overview.mp4`, `infographic.png`, `slides.pdf`)
2. Create `docs/artefacts/index.html` player page
3. Update README.md with "Repo Deep Dive" links
4. Enable GitHub Pages via API
5. Git commit and push
6. Verify the Pages URL returns 200

## UC3: Set up GitHub Pages only

> Artefacts are already in `docs/artefacts/`, you just need the player page.

```bash
repo-artefacts pages ./my-repo
```

This creates the player page and README links without touching NotebookLM.

## UC4: List and manage notebooks

```bash
# List all notebooks
repo-artefacts list

# List sources in a notebook
repo-artefacts list -n $NOTEBOOK_ID

# Delete a notebook
repo-artefacts delete -n $NOTEBOOK_ID
```

## UC5: Update README after manual artefact changes

```bash
repo-artefacts update-readme --artefacts-dir ./docs/artefacts
```

## Standard Artefact Filenames

All commands expect these filenames in `docs/artefacts/`:

| File | Type |
|------|------|
| `audio_overview.m4a` | Audio deep dive |
| `video_overview.mp4` | Video explainer |
| `infographic.png` | Architecture infographic |
| `slides.pdf` | Slide deck |
