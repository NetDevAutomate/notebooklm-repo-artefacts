---
title: "feat: Centralised Artefact Store"
type: feat
status: active
date: 2026-03-11
origin: docs/brainstorms/2026-03-11-centralised-artefact-store-brainstorm.md
---

# feat: Centralised Artefact Store

## Overview

Add `--store` support to `repo-artefacts pipeline` so artefacts are published to a dedicated `artefact-store` repo instead of being committed into source repos. Includes config system for default store, validation/cleanup subcommands, and a migration path for existing repos.

## Problem Statement

Binary artefacts (50-150MB per repo) committed directly into source repos cause 385MB clones, LFS pointer mismatches, and permanent git history bloat. Three repos are affected today, with more planned.

## Architecture

```
repo-artefacts pipeline /path/to/repo --store NetDevAutomate/artefact-store
                │
                ├── Step 1-3: Upload → Generate → Download (unchanged)
                │
                ├── Step 4: Clone artefact-store (shallow)
                │   └── Copy artefacts to <repo-name>/artefacts/
                │   └── Generate player index.html
                │   └── Update manifest.json
                │   └── Commit & push artefact-store
                │
                ├── Step 5: Update source repo README
                │   └── URLs point to artefacts.netdevautomate.dev/<repo>/artefacts/
                │   └── Commit & push README only (no binary files)
                │
                └── Step 6: Verify artefact-store Pages URLs
```

### Store Structure

```
artefact-store/
├── index.html                          ← landing page (reads manifest.json)
├── manifest.json                       ← auto-updated by pipeline
├── CNAME                               ← artefacts.netdevautomate.dev
├── Socratic-Study-Mentor/
│   └── artefacts/
│       ├── index.html                  ← player page (from template.html)
│       ├── audio_overview.mp3
│       ├── video_overview.mp4
│       ├── infographic.png
│       └── slides.pdf
├── notebooklm-pdf-by-chapters/
│   └── artefacts/
│       └── ...
└── Agent-Speaker/
    └── artefacts/
        └── ...
```

**URLs**: `https://artefacts.netdevautomate.dev/<repo-name>/artefacts/`

## Technical Approach

### New Module: `src/repo_artefacts/store.py`

Pure logic for artefact-store operations. No CLI, no Rich — raises exceptions for errors.

```python
"""Artefact store operations: clone, publish, manifest updates."""

STORE_CACHE_DIR = Path("~/.cache/repo-artefacts/stores").expanduser()

def clone_or_pull_store(store_slug: str, token: str | None = None) -> Path:
    """Shallow clone artefact-store repo, or pull if cached.

    Args:
        store_slug: GitHub org/repo (e.g., "NetDevAutomate/artefact-store")
        token: GitHub token for auth (uses HTTPS clone with token)

    Returns:
        Path to local store checkout.

    Uses --depth 1 for fast clone. Cached in STORE_CACHE_DIR/<slug>.
    On subsequent calls, does git pull --ff-only.
    """

def publish_to_store(
    store_path: Path,
    repo_name: str,
    artefacts_dir: Path,
    template_html: str,
) -> str:
    """Copy artefacts to store and generate player page.

    Args:
        store_path: Path to local store checkout.
        repo_name: Name of the source repo (directory name in store).
        artefacts_dir: Path to local artefacts to publish.
        template_html: Player template HTML string.

    Returns:
        Base URL for this repo's artefacts.

    Flow:
        1. Create <store>/<repo_name>/artefacts/ directory
        2. Copy all artefact files from artefacts_dir
        3. Write index.html from template (same as pages.py:setup_pages L204-208)
        4. Update manifest.json (add/update entry for this repo)
    """

def update_manifest(store_path: Path, repo_name: str, repo_info: dict) -> None:
    """Add or update a repo entry in manifest.json.

    Args:
        store_path: Path to local store checkout.
        repo_name: Repo identifier.
        repo_info: Dict with keys: title, description, artefacts, updated.

    Reads existing manifest, upserts entry by repo_name, writes back.
    """

def commit_and_push_store(store_path: Path, repo_name: str) -> bool:
    """Stage, commit, and push store changes.

    Stages only: <repo_name>/artefacts/, manifest.json
    Commit message: "artefacts: update <repo_name>"
    Returns True on success, False on failure.
    On push conflict: pull --rebase, then retry push once.
    """

def list_store_repos(store_path: Path) -> list[str]:
    """List repo directories in the store (for clean command)."""

def remove_store_repo(store_path: Path, repo_name: str) -> None:
    """Remove a repo's artefacts directory from the store."""
```

### New Module: `src/repo_artefacts/config.py`

```python
"""User configuration for repo-artefacts."""

CONFIG_DIR = Path("~/.config/repo-artefacts").expanduser()
CONFIG_FILE = CONFIG_DIR / "config.yaml"

@dataclass
class Config:
    default_store: str | None = None      # e.g., "NetDevAutomate/artefact-store"
    store_cache_dir: Path = STORE_CACHE_DIR

def load_config() -> Config:
    """Load config from ~/.config/repo-artefacts/config.yaml.

    Returns default Config if file doesn't exist.
    """

def save_config(config: Config) -> None:
    """Save config to YAML file. Creates directory if needed."""
```

Config file format:
```yaml
# ~/.config/repo-artefacts/config.yaml
default_store: NetDevAutomate/artefact-store
```

### Modified: `src/repo_artefacts/pages.py`

**`setup_pages()` (L198-234)** — Add `store_base_url` parameter:

```python
def setup_pages(
    repo_root: Path,
    org: str,
    repo: str,
    store_base_url: str | None = None,  # NEW
) -> str:
    """Set up GitHub Pages player and update README.

    If store_base_url is provided:
      - Skip writing index.html to source repo (store has it)
      - Skip enable_github_pages on source repo
      - Use store_base_url for README links
    Otherwise: existing behaviour (write to docs/artefacts/).
    """
```

**`_build_readme_block()` (L26-45)** — No changes needed. Already takes `base_url` parameter. When using store, pass `https://artefacts.netdevautomate.dev/<repo>/artefacts/` instead of `https://<org>.github.io/<repo>/artefacts/`.

### Modified: `src/repo_artefacts/publish.py`

**`TOOL_OUTPUTS` (L76)** — When using store, only stage `README.md`:

```python
TOOL_OUTPUTS_DEFAULT = ["docs/artefacts/", "README.md"]
TOOL_OUTPUTS_STORE = ["README.md"]  # No binary files in source repo
```

**`git_commit_and_push()` (L115-166)** — Add `outputs` parameter:

```python
def git_commit_and_push(
    repo_root: Path,
    message: str,
    remote: str = "origin",
    branch: str | None = None,
    outputs: list[str] | None = None,  # NEW — overrides TOOL_OUTPUTS
) -> bool:
```

**`verify_pages()` (L35-72)** — No changes needed. Already takes arbitrary URL.

### Modified: `src/repo_artefacts/cli.py`

**`pipeline` command (L308-507)** — Add `--store` option:

```python
@app.command()
@_handle_errors
def pipeline(
    repo_path: str = typer.Argument("."),
    # ... existing args ...
    store: str | None = typer.Option(
        None,
        "--store",
        "-s",
        help="Publish artefacts to external store repo (org/repo). "
             "Defaults to config file value if set.",
    ),
) -> None:
```

**Flow change when `--store` is provided** (or configured as default):

Replace steps 5-7 (L452-474) with:

```python
# Step 5: Publish to artefact store
if store_slug:
    from repo_artefacts.store import (
        clone_or_pull_store,
        publish_to_store,
        commit_and_push_store,
    )
    token = get_github_token()
    store_path = clone_or_pull_store(store_slug, token)
    store_base_url = publish_to_store(store_path, repo, artefacts_dir, template_html)
    push_ok = commit_and_push_store(store_path, repo)
    if not push_ok:
        console.print("[red]Failed to push artefact store.[/red]")
        # Don't delete notebook — artefacts only exist locally
        raise typer.Exit(1)

    # Step 6: Update source repo README with store URLs
    setup_pages(root, org, repo, store_base_url=store_base_url)
    push_ok = git_commit_and_push(
        root, f"docs: update artefact links for {repo}", remote,
        outputs=TOOL_OUTPUTS_STORE,
    )

    # Step 7: Verify store deployment
    verified_ok, verified_kinds = verify_pages(
        store_base_url, max_wait=verify_timeout, artefact_urls=artefact_urls
    )
else:
    # Existing local behaviour (unchanged)
    ...
```

**`publish` command (L236-303)** — Same `--store` option with same behaviour.

**Store resolution** (top of pipeline/publish):

```python
# Resolve store: explicit flag > config file > None (local mode)
from repo_artefacts.config import load_config
cfg = load_config()
store_slug = store or cfg.default_store
```

### New Command: `validate`

```python
@app.command()
@_handle_errors
def validate(
    repo_path: str = typer.Argument(".", help="Repo to validate README links."),
    all_repos: bool = typer.Option(
        False, "--all", "-a", help="Validate all repos in the artefact store."
    ),
    store: str | None = typer.Option(None, "--store", "-s"),
) -> None:
    """Check that artefact URLs in README are reachable.

    For a single repo: parse README, HEAD each artefact URL.
    With --all: iterate store manifest and check every repo.
    """
```

Implementation: Parse `<!-- ARTEFACTS:START -->` block from README, extract URLs with regex, HTTP HEAD each one. Report table of results. Exit 1 if any broken.

### New Command: `clean`

```python
@app.command()
@_handle_errors
def clean(
    store: str | None = typer.Option(None, "--store", "-s", help="Store repo slug."),
    delete: bool = typer.Option(False, "--delete", help="Remove orphaned artefacts."),
) -> None:
    """Find orphaned artefacts in the store.

    Lists artefact directories that no longer have a matching source
    repo on GitHub. With --delete, removes them and pushes.
    """
```

Implementation: `list_store_repos()` → for each, `gh api repos/{org}/{name}` → if 404, mark as orphan. Report table. If `--delete`, remove + commit + push.

### New Command: `migrate`

```python
@app.command()
@_handle_errors
def migrate(
    repo_path: str = typer.Argument(".", help="Repo to migrate artefacts from."),
    store: str | None = typer.Option(None, "--store", "-s"),
) -> None:
    """Move artefacts from source repo to artefact store.

    Copies docs/artefacts/* to the store, updates README URLs, and removes
    docs/artefacts/ from the source repo. Does NOT rewrite git history —
    suggest git-filter-repo command for that.
    """
```

Implementation:
1. `check_artefacts(repo_root / "docs/artefacts")` — find what exists
2. `clone_or_pull_store()` → `publish_to_store()` → `commit_and_push_store()`
3. `setup_pages(store_base_url=...)` — rewrite README URLs
4. `git rm -r docs/artefacts/` (keep .gitattributes)
5. `git_commit_and_push()` with outputs `["README.md"]` + manual staging of removal
6. Print: `"To shrink repo history, run: git filter-repo --path docs/artefacts/ --invert-paths"`

## Implementation Phases

### Phase 0: Config System

**New file:** `src/repo_artefacts/config.py`

Deliverables:
- `Config` dataclass with `default_store` field
- `load_config()` / `save_config()` with YAML
- Add `pyyaml` to dependencies in `pyproject.toml`

**Test file:** `tests/test_config.py`
- Round-trip save/load
- Missing file returns defaults
- Creates directory if needed

### Phase 1: Store Module

**New file:** `src/repo_artefacts/store.py`

Deliverables:
- `clone_or_pull_store()` — shallow clone with token auth, cached in `~/.cache`
- `publish_to_store()` — copy artefacts, write player page, update manifest
- `update_manifest()` — upsert entry in manifest.json
- `commit_and_push_store()` — stage, commit, push with conflict retry
- `list_store_repos()` / `remove_store_repo()` — for clean command

**Test file:** `tests/test_store.py`
- `publish_to_store()` creates correct directory structure
- `update_manifest()` adds new entry, updates existing, preserves others
- `commit_and_push_store()` stages only relevant paths

**Key implementation notes:**
- Clone via HTTPS with token: `git clone --depth 1 https://x-access-token:{token}@github.com/{slug}.git`
- Cache dir: `~/.cache/repo-artefacts/stores/{org}/{repo}/`
- Conflict handling: if push fails, `git pull --rebase` then retry once
- Template HTML: read from package resources (same as `pages.py:L204`)
- Manifest update: read JSON, find entry by `name` field, upsert, write with `indent=2`

### Phase 2: Pipeline Integration

**Modified files:** `cli.py`, `pages.py`, `publish.py`

Changes:
1. Add `--store` option to `pipeline` command (L308)
2. Add `--store` option to `publish` command (L236)
3. Resolve store: `--store` flag > `config.default_store` > None
4. When store is set:
   - Download artefacts to temp dir (or keep existing local dir)
   - Call `store.publish_to_store()` + `store.commit_and_push_store()`
   - Call `pages.setup_pages()` with `store_base_url`
   - Call `publish.git_commit_and_push()` with `outputs=["README.md"]`
   - Verify store URLs instead of source repo URLs
5. Add `store_base_url` parameter to `setup_pages()` (skip index.html + Pages API when using store)
6. Add `outputs` parameter to `git_commit_and_push()` (override TOOL_OUTPUTS)

**Test updates:** `tests/test_cli.py`
- Pipeline with `--store` writes to store, not source repo
- Pipeline without `--store` uses existing behaviour (regression)
- Store resolution: explicit > config > None

### Phase 3: Validate & Clean Commands

**Modified file:** `cli.py`

New commands:
- `validate` — parse README, HEAD artefact URLs, report broken links
- `clean` — list store directories, check source repos exist, report/delete orphans

**Test file:** `tests/test_validate.py`, `tests/test_clean.py`
- Validate with all links working → exit 0
- Validate with broken link → exit 1
- Clean with no orphans → "All repos have matching source repos"
- Clean with orphan + `--delete` → removes directory

### Phase 4: Migrate Command

**Modified file:** `cli.py`

New command:
- `migrate` — copy artefacts from source repo to store, update README, remove from source

**Test file:** `tests/test_migrate.py`
- Artefacts copied to store correctly
- README URLs updated to store URLs
- `docs/artefacts/` removed from source (git rm)
- Prints filter-repo suggestion

### Phase 5: Migration Execution

Not code — run `migrate` on the 3 repos:

```bash
# For each repo
repo-artefacts migrate /path/to/repo --store NetDevAutomate/artefact-store

# Then clean git history (per-repo, requires force push)
cd /path/to/repo
git filter-repo --path docs/artefacts/ --invert-paths
git push --force-with-lease
```

Order: Agent-Speaker (smallest), notebooklm-pdf-by-chapters, Socratic-Study-Mentor (largest).

## Acceptance Criteria

### Core
- [ ] `pipeline --store org/repo` publishes artefacts to store, not source repo
- [ ] Source repo gets README links only — zero binary files committed
- [ ] Player page works at `artefacts.netdevautomate.dev/<repo>/artefacts/`
- [ ] Landing page auto-updates when new repos are published
- [ ] `pipeline` without `--store` retains existing local behaviour
- [ ] Config file `default_store` removes need for `--store` flag

### Validation & Cleanup
- [ ] `validate` catches broken artefact links in README
- [ ] `validate --all` checks all repos in store
- [ ] `clean` identifies orphaned store directories
- [ ] `clean --delete` removes orphans and pushes

### Migration
- [ ] `migrate` moves artefacts from source to store cleanly
- [ ] `migrate` updates source README with store URLs
- [ ] `migrate` removes docs/artefacts/ from source repo
- [ ] All 3 existing repos migrated successfully

### Safety
- [ ] Store clone uses `--depth 1` (fast, regardless of store size)
- [ ] Store push conflict handled with pull + retry
- [ ] Source repo only stages README.md when using store
- [ ] Failed store push prevents notebook cleanup (artefacts preserved locally)

## Dependencies & Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Store push conflict (two pipelines) | Low | Medium | Pull --rebase + retry once. Document as unsupported for concurrent runs. |
| GitHub Pages propagation delay | Medium | Low | Existing verify polling handles this. |
| Store repo grows >5GB | Low (years away) | High | Migrate to S3/R2 + CloudFront when needed. CNAME makes URL change transparent. |
| git filter-repo on source repos | Low | High | Done manually per-repo, `--force-with-lease` safety. Document backup steps. |
| DNS propagation delay for CNAME | Low | Low | One-time setup. Script is idempotent. |

## Commit Strategy

6 commits matching phases:
1. `feat: add config system for default store` (Phase 0)
2. `feat: add store module for artefact-store operations` (Phase 1 + tests)
3. `feat: integrate --store with pipeline and publish commands` (Phase 2 + tests)
4. `feat: add validate and clean commands` (Phase 3 + tests)
5. `feat: add migrate command` (Phase 4 + tests)
6. `chore: migrate existing repos to artefact-store` (Phase 5 — no code, just execution)

## References

### Internal
- Brainstorm: `docs/brainstorms/2026-03-11-centralised-artefact-store-brainstorm.md`
- Pipeline flow: `src/repo_artefacts/cli.py:308-507`
- Pages setup: `src/repo_artefacts/pages.py:198-234`
- Git operations: `src/repo_artefacts/publish.py:115-166`
- README block: `src/repo_artefacts/pages.py:26-45`
- Player template: `src/repo_artefacts/template.html`
- Artefact detection: `src/repo_artefacts/publish.py:25-32`
- Existing plans: `docs/plans/2026-03-10-feat-pipeline-robustness-sweep-plan.md`

### External
- artefact-store repo: `https://github.com/NetDevAutomate/artefact-store`
- Cloudflare DNS script: `~/code/personal/tools/cloudflare/dns/setup_artefacts_cname.py`
- GitHub Pages custom domains: https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site
