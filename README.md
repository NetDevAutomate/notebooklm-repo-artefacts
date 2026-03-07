# repo-artefacts

Generate NotebookLM artefacts — audio walkthroughs, video explainers, slide decks, and infographics — from any git repository.

## How It Works

1. **Collect** — Scans a git repo and assembles key files (README, docs, config, source code) into a single markdown document
2. **Upload** — Sends the document to Google NotebookLM as a notebook source
3. **Generate** — Creates audio, video, slide deck, and/or infographic artefacts via NotebookLM
4. **Download** — Fetches the generated artefacts locally

## Installation

Requires Python 3.11+.

```bash
# From local checkout
uv tool install .

# From git
uv tool install git+https://github.com/youruser/repo-artefacts.git
```

## Prerequisites

For NotebookLM features, authenticate first:

```bash
pip install notebooklm-py[browser]
notebooklm login
```

## Usage

### `process` — Collect repo content and upload to NotebookLM

```bash
# Current directory
repo-artefacts process

# Specific repo
repo-artefacts process /path/to/repo

# Custom output directory
repo-artefacts process /path/to/repo -o ./output

# Upload to existing notebook
repo-artefacts process -n NOTEBOOK_ID
```

### `generate` — Create artefacts from a notebook

```bash
# Generate all artefact types (default)
repo-artefacts generate -n NOTEBOOK_ID

# Specific types
repo-artefacts generate -n NOTEBOOK_ID --audio --slides

# Just video
repo-artefacts generate -n NOTEBOOK_ID --video
```

Available flags: `--audio`, `--video`, `--slides`, `--infographic`, `--all`

### `download` — Fetch generated artefacts

```bash
repo-artefacts download -n NOTEBOOK_ID -o ./docs/artefacts
```

### `list` — View notebooks and sources

```bash
# List all notebooks
repo-artefacts list

# List sources in a notebook
repo-artefacts list -n NOTEBOOK_ID
```

## Typical Workflow

```bash
# 1. Collect and upload a repo
repo-artefacts process /path/to/interesting-repo

# 2. Find the notebook ID
repo-artefacts list

# 3. Generate all artefacts
repo-artefacts generate -n NOTEBOOK_ID

# 4. Download everything
repo-artefacts download -n NOTEBOOK_ID -o ./docs/artefacts
```

## What Gets Collected

The collector scans the repo and includes files in this priority order:

1. **README** — `README.md`, `README.rst`, or `README.txt`
2. **Docs** — All `.md` files under `docs/`
3. **Project config** — `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, etc.
4. **Source code** — Files under `src/` (or repo root) with common extensions, limited to files under 500 lines

Total output is capped at 500KB. README and docs are always included in full; source files are truncated first if the limit is hit.

## License

MIT
