# notebooklm-repo-artefacts

Generate NotebookLM artefacts — audio walkthroughs, video explainers, slide decks, and infographics — from any git repository.

## How It Works

```
repo → collect files → render to PDF (with Mermaid) → upload to NotebookLM → generate artefacts → download
```

1. **Collect** — Scans a git repo and assembles key files (README, docs, config, source code) into a single markdown document
2. **Render** — Converts the markdown to a fully rendered PDF using Chromium via Playwright, including Mermaid diagrams and tables
3. **Upload** — Sends the PDF to Google NotebookLM as a notebook source
4. **Generate** — Creates audio, video, slide deck, and/or infographic artefacts via NotebookLM
5. **Download** — Fetches the generated artefacts locally
6. **Update README** — Optionally inserts an artefacts listing into your project's README

## Installation

Requires Python 3.11+.

```bash
# From PyPI (when published)
uv tool install notebooklm-repo-artefacts

# From GitHub
uv tool install git+https://github.com/NetDevAutomate/notebooklm-repo-artefacts.git

# From local checkout
uv tool install .
```

## Prerequisites

Install Chromium for PDF rendering (used by Playwright):

```bash
playwright install chromium
```

For NotebookLM features (`process`, `list`, `generate`, `download`, `delete`), authenticate first:

```bash
uv pip install notebooklm-py[browser]
notebooklm login
```

This opens a browser for Google cookie-based auth. Credentials are stored locally.

## Usage

### `pipeline` — Full end-to-end (recommended)

One command to go from repo to hosted artefacts:

```bash
# Full pipeline: collect → upload → generate → download → pages → push → verify → cleanup
repo-artefacts pipeline /path/to/repo

# Use an existing notebook (skips upload)
repo-artefacts pipeline /path/to/repo -n NOTEBOOK_ID

# Only generate audio and video (skip slides/infographic)
repo-artefacts pipeline /path/to/repo --audio --video

# Generate everything except infographic (preserve quota)
repo-artefacts pipeline /path/to/repo --exclude infographic

# Resume: only generate artefacts not yet completed in the notebook
repo-artefacts pipeline /path/to/repo -n NOTEBOOK_ID --resume

# Keep the notebook after publishing
repo-artefacts pipeline /path/to/repo --keep-notebook
```

See [Pipeline Architecture](docs/pipeline.md) for the full flow diagram and comparison with `publish`.

### `process` — Collect repo content and upload to NotebookLM

```bash
repo-artefacts process /path/to/repo
repo-artefacts process /path/to/repo -o ./output
repo-artefacts process -n NOTEBOOK_ID  # upload to existing notebook
```

### `generate` — Create artefacts from a notebook

```bash
repo-artefacts generate -n NOTEBOOK_ID
repo-artefacts generate -n NOTEBOOK_ID --audio --slides
repo-artefacts generate -n NOTEBOOK_ID --timeout 1200
```

Available flags: `--audio`, `--video`, `--slides`, `--infographic`, `--all`

### `download` — Fetch generated artefacts

```bash
repo-artefacts download -n NOTEBOOK_ID -o ./docs/artefacts
```

### `list` — View notebooks and sources

```bash
repo-artefacts list                    # all notebooks
repo-artefacts list -n NOTEBOOK_ID     # sources in a notebook
```

### `delete` — Remove a notebook

```bash
repo-artefacts delete -n NOTEBOOK_ID
```

### `publish` — Generate, publish, and verify

```bash
repo-artefacts publish /path/to/repo -n NOTEBOOK_ID
repo-artefacts publish /path/to/repo --skip-generate  # use existing artefacts
```

### `pages` — Set up GitHub Pages player

```bash
repo-artefacts pages /path/to/repo
repo-artefacts pages /path/to/repo --org MyOrg --repo my-repo
```

### Using `NOTEBOOK_ID` environment variable

All commands that accept `-n NOTEBOOK_ID` also read from the `NOTEBOOK_ID` environment variable:

```bash
export NOTEBOOK_ID=ba6fa92e-f174-4a77-8fc6-fc4fc12a625d
repo-artefacts generate
repo-artefacts download -o ./docs/artefacts
```

## Options Reference

| Option | Commands | Description | Default |
|---|---|---|---|
| `repo_path` | process, pipeline, publish, pages | Path to git repository (positional) | `.` |
| `-o, --output-dir` | process, download | Output directory | `./docs/artefacts` |
| `-n, --notebook-id` | process, generate, download, list, delete, publish, pipeline | NotebookLM notebook ID (or `NOTEBOOK_ID` env var) | — |
| `--audio` | generate, pipeline | Generate audio overview | — |
| `--video` | generate, pipeline | Generate video explainer | — |
| `--slides` | generate, pipeline | Generate slide deck | — |
| `--infographic` | generate, pipeline | Generate infographic | — |
| `--all` | generate | Generate all artefact types (default if none specified) | — |
| `-t, --timeout` | generate, publish, pipeline | Timeout in seconds per artefact | `900` |
| `--exclude` | pipeline | Artefact types to skip (repeatable) | — |
| `--resume` | pipeline | Only generate artefacts not yet completed (note: default mode already skips completed artefacts) | `false` |
| `--keep-notebook` | pipeline | Don't delete the notebook after publishing | `false` |
| `-r, --remote` | publish, pipeline | Git remote to push to | `origin` |
| `--skip-generate` | publish | Skip artefact generation (use existing files) | `false` |
| `--skip-verify` | publish | Skip page verification | `false` |
| `--verify-timeout` | publish | Max seconds to wait for Pages deployment | `120` |

## What Gets Collected

The collector scans the repo and includes files in this priority order:

1. **README** — `README.md`, `README.rst`, or `README.txt`
2. **Docs** — All `.md`, `.rst`, `.txt` files under `docs/`
3. **Project config** — `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Makefile`, etc.
4. **Source code** — Files under `src/` (or repo root) with common extensions, limited to files under 500 lines

Total output is capped at **500KB**. README and docs are always included in full; source files are truncated first if the limit is hit.

Supported source extensions: `.py`, `.ts`, `.js`, `.rs`, `.java`, `.go`, `.rb`, `.kt`, `.swift`, `.c`, `.cpp`, `.h`, `.hpp`, `.cs`, `.scala`, `.ex`, `.exs`, `.clj`, `.zig`, `.lua`, `.sh`, `.bash`

Skipped directories: `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `dist`, `build`, `.tox`, `.eggs`, `target`, `.next`, `.nuxt`, `vendor`

<!-- ARTEFACTS:START -->
## Generated Artefacts

> 🔍 **Explore this project** — AI-generated overviews via [Google NotebookLM](https://notebooklm.google.com)

| | |
|---|---|
| 🎧 **[Listen to the Audio Overview](https://netdevautomate.github.io/notebooklm-repo-artefacts/artefacts/)** | Two AI hosts discuss the project — great for commutes |
| 🎬 **[Watch the Video Overview](https://netdevautomate.github.io/notebooklm-repo-artefacts/artefacts/#video)** | Visual walkthrough of architecture and concepts |
| 🖼️ **[View the Infographic](https://netdevautomate.github.io/notebooklm-repo-artefacts/artefacts/#infographic)** | Architecture and flow at a glance |
| 📊 **[Browse the Slide Deck](https://netdevautomate.github.io/notebooklm-repo-artefacts/artefacts/#slides)** | Presentation-ready project overview |

*Generated by [notebooklm-repo-artefacts](https://github.com/NetDevAutomate/notebooklm-repo-artefacts)*
<!-- ARTEFACTS:END -->

## Acknowledgements

> **Special thanks to [Teng Lin](https://github.com/teng-lin)** for creating the excellent [notebooklm-py](https://github.com/teng-lin/notebooklm-py) library, which powers all NotebookLM integration in this tool. His work in reverse-engineering and wrapping the NotebookLM API made this project possible.

## License

MIT — see [LICENSE](LICENSE) for details.
