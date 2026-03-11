---
title: "feat: Centralised Artefact Store"
type: feat
status: brainstormed
date: 2026-03-11
---

# Brainstorm: Centralised Artefact Store

## Problem

`repo-artefacts pipeline` commits binary artefacts (audio, video, slides, infographic — 50-150MB per repo) directly into each source repo's `docs/artefacts/`. This causes:

1. **Clone bloat**: Socratic-Study-Mentor is 385MB (should be <5MB)
2. **LFS pain**: Files committed before LFS configured → pointer mismatches on clone
3. **History pollution**: Old artefact versions permanently bloat git objects
4. **Per-repo overhead**: Every repo carries its own binary burden + Pages config

## Proposed Solution

A dedicated `NetDevAutomate/artefact-store` repo with GitHub Pages enabled, served at `artefacts.netdevautomate.dev`. Source repos link to the store instead of carrying binary files.

### Key Decisions

| Decision | Chosen | Alternatives Considered |
|----------|--------|------------------------|
| Storage location | Dedicated repo (artefact-store) | Same-repo gh-pages branch (still bloats), S3/R2 (overkill for now) |
| Custom domain | artefacts.netdevautomate.dev (Cloudflare DNS) | {org}.github.io/artefact-store (works but not future-proof for CDN migration) |
| Default behaviour | `--store` configurable as default via config.yaml | Always explicit flag (tedious), always store (breaks existing users) |
| Landing page | Auto-generated from manifest.json | Static README (doesn't auto-update), server-side (GitHub Pages is static) |
| `--update`/validate/clean | Subcommands of repo-artefacts | Separate tool (over-scoped for single concern) |
| LFS in store | No LFS | LFS (pointless on a store-only repo nobody clones for dev) |

### Repos Affected

- NetDevAutomate/Socratic-Study-Mentor
- NetDevAutomate/notebooklm-pdf-by-chapters
- NetDevAutomate/Agent-Speaker
- Any future repo using repo-artefacts

### Infrastructure Created

- GitHub repo: `NetDevAutomate/artefact-store` (done)
- Cloudflare CNAME: `artefacts.netdevautomate.dev → NetDevAutomate.github.io` (script ready)
- GitHub Pages enabled on artefact-store (done)
- Landing page with manifest-driven card grid (done)
