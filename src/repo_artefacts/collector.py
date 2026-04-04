"""Collect key files from a git repository into a single markdown document.

Uses a priority-based pattern matching system:
  1. Exact matches (README, agent instructions) — highest priority
  2. Glob patterns for docs, config, and source files
  3. Size budget enforced across all categories
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from repo_artefacts.console import get_console
from repo_artefacts.exceptions import CollectionError

MAX_TOTAL_BYTES = 700 * 1024  # 700KB — enough for monorepo docs + key source files
MAX_SOURCE_LINES = 500
MAX_LINE_LENGTH = 10_000  # reject minified/generated files
MAX_FILE_BYTES = 512 * 1024  # per-file size guard
SOURCE_EXTENSIONS = {
    ".py",
    ".ts",
    ".js",
    ".rs",
    ".java",
    ".go",
    ".rb",
    ".kt",
    ".swift",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".scala",
    ".ex",
    ".exs",
    ".clj",
    ".zig",
    ".lua",
    ".sh",
    ".bash",
}

# Directories to skip entirely during tree walk
SKIP_DIRS = frozenset(
    {
        ".git",
        ".claude",
        ".github",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
        ".eggs",
        "target",
        ".next",
        ".nuxt",
        "vendor",
        ".mypy_cache",
        ".pytest_cache",
        "htmlcov",
        "site-packages",
    }
)

# Subdirectories within docs/ to skip (brainstorming, research, internal notes)
SKIP_DOC_SUBDIRS = frozenset(
    {
        "internal",
        "research",
        "brainstorming",
        "notes",
        "drafts",
        "archive",
        "archived",
    }
)


@dataclass
class CollectionPattern:
    """A file collection rule with priority and constraints."""

    name: str
    # Glob patterns relative to repo root (e.g., "docs/**/*.md")
    globs: list[str]
    # Regex patterns that must match the relative path (after glob match)
    include_regex: list[str] = field(default_factory=list)
    # Regex patterns that exclude a match even if glob/include match
    exclude_regex: list[str] = field(default_factory=list)
    # Max lines per file (None = no limit, uses _read_safe default)
    max_lines: int | None = None
    # Priority: lower number = collected first (1 = highest)
    priority: int = 10


# Ordered collection rules — higher priority rules run first
COLLECTION_RULES: list[CollectionPattern] = [
    # 1. README (exact match, full content)
    CollectionPattern(
        name="README",
        globs=["README.md", "README.rst", "README.txt", "README"],
        priority=1,
    ),
    # 2. Agent instruction files (exact match, full content)
    CollectionPattern(
        name="Agent instructions",
        globs=["AGENTS.md", "CLAUDE.md", "GEMINI.md", "CODING.md", "DEVELOPMENT.md"],
        priority=2,
    ),
    # 3. Root-level project docs (markdown files at repo root)
    CollectionPattern(
        name="Root docs",
        globs=["*.md"],
        exclude_regex=[
            r"^README",
            r"^AGENTS",
            r"^CLAUDE",
            r"^GEMINI",
            r"^CODING",
            r"^DEVELOPMENT",
        ],
        priority=3,
    ),
    # 4. Public documentation (docs/**/*.md, excluding internal/research)
    CollectionPattern(
        name="Documentation",
        globs=["docs/**/*.md", "doc/**/*.md"],
        exclude_regex=[
            r"^docs/internal/",
            r"^docs/research/",
            r"^docs/brainstorming/",
            r"^docs/notes/",
            r"^docs/drafts/",
            r"^docs/archive/",
        ],
        priority=4,
    ),
    # 5. Project configuration files
    CollectionPattern(
        name="Configuration",
        globs=[
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "Cargo.toml",
            "package.json",
            "go.mod",
            "build.gradle",
            "pom.xml",
            "Makefile",
            "CMakeLists.txt",
            "deno.json",
            "mkdocs.yml",
            ".pre-commit-config.yaml",
            "ruff.toml",
            ".ruff.toml",
            "pyrightconfig.json",
            "tsconfig.json",
            "cliff.toml",
        ],
        priority=5,
    ),
    # 6. Source code (packages/*/src/ and src/)
    CollectionPattern(
        name="Source code",
        globs=[
            "packages/*/src/**/*",
            "src/**/*",
        ],
        include_regex=[
            # Only collect files with known source extensions
            r"\.(py|ts|js|rs|java|go|rb|kt|swift|c|cpp|h|hpp|cs|scala|ex|exs|clj|zig|lua|sh|bash)$",
        ],
        exclude_regex=[
            # Skip test files (they're large and less useful for overview)
            r"test_",
            r"_test\.",
            r"/tests/",
            # Skip generated/migration files
            r"/migrations/",
            # Skip __pycache__ and build artifacts (belt and braces with SKIP_DIRS)
            r"__pycache__",
            r"\.pyc$",
        ],
        max_lines=MAX_SOURCE_LINES,
        priority=6,
    ),
]


def _is_git_repo(path: Path) -> bool:
    """Check if path is a git repo root (handles regular repos, worktrees, bare repos)."""
    git_path = path / ".git"
    if git_path.is_dir() or git_path.is_file():
        return True
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def _read_safe(path: Path, max_lines: int | None = None) -> str | None:
    """Read a file safely with size, line count, and line length guards.

    Returns None if the file cannot be read, exceeds max_lines,
    exceeds MAX_FILE_BYTES, or contains lines longer than MAX_LINE_LENGTH
    (indicating minified/generated content).
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
    except OSError:
        return None

    try:
        if max_lines:
            lines: list[str] = []
            with path.open(encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        return None
                    if len(line) > MAX_LINE_LENGTH:
                        return None  # minified/generated file
                    lines.append(line)
            return "".join(lines)
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _matches_patterns(rel_path: str, include_regex: list[str], exclude_regex: list[str]) -> bool:
    """Check if a relative path matches the include/exclude regex patterns."""
    if include_regex and not any(re.search(p, rel_path) for p in include_regex):
        return False
    return not (exclude_regex and any(re.search(p, rel_path) for p in exclude_regex))


def _collect_files(repo_path: Path) -> list[tuple[str, Path, int | None]]:
    """Walk the repo and categorise files by matching against COLLECTION_RULES.

    Returns list of (rule_name, file_path, max_lines) sorted by priority,
    then by relative path within each rule. Deduplicates so each file is
    only collected once (first matching rule wins).
    """
    seen: set[Path] = set()
    results: list[tuple[int, str, Path, int | None]] = []  # (priority, rule_name, path, max_lines)

    for rule in sorted(COLLECTION_RULES, key=lambda r: r.priority):
        for glob_pattern in rule.globs:
            for matched_path in repo_path.glob(glob_pattern):
                if not matched_path.is_file():
                    continue
                if matched_path in seen:
                    continue

                rel = str(matched_path.relative_to(repo_path))

                # Check include/exclude regex patterns
                if not _matches_patterns(rel, rule.include_regex, rule.exclude_regex):
                    continue

                # Check if any parent directory is in SKIP_DIRS
                parts = matched_path.parts
                if any(part in SKIP_DIRS for part in parts):
                    continue

                seen.add(matched_path)
                results.append((rule.priority, rule.name, matched_path, rule.max_lines))

    # Sort by priority then by path for deterministic ordering
    results.sort(key=lambda x: (x[0], str(x[2])))
    return [(name, path, max_lines) for _, name, path, max_lines in results]


def collect_repo_content(repo_path: Path, output_path: Path) -> Path:
    """Collect key files from a git repo into a single markdown document.

    Uses pattern-based collection with priority ordering:
      1. README
      2. Agent instruction files (AGENTS.md, CLAUDE.md, etc.)
      3. Root-level project docs (CONTRIBUTING.md, etc.)
      4. Public documentation (docs/**/*.md, excluding internal/)
      5. Configuration files (pyproject.toml, etc.)
      6. Source code (packages/*/src/, src/)

    Truncates source files and stops when total content exceeds MAX_TOTAL_BYTES.

    Args:
        repo_path: Path to the git repository root.
        output_path: Path to write the combined markdown file.

    Returns:
        The output_path written to.

    Raises:
        CollectionError: If repo_path doesn't exist, isn't a directory, or isn't a git repo.
    """
    if not repo_path.is_dir():
        raise CollectionError(f"'{repo_path}' is not a directory")
    if not _is_git_repo(repo_path):
        raise CollectionError(f"'{repo_path}' is not a git repository")

    sections: list[tuple[str, str]] = []  # (heading, content)
    repo_name = repo_path.resolve().name

    # Collect all matching files, sorted by priority
    matched_files = _collect_files(repo_path)

    total_bytes = 0
    for _rule_name, file_path, max_lines in matched_files:
        content = _read_safe(file_path, max_lines=max_lines)
        if content is None:
            continue

        content_bytes = len(content.encode("utf-8"))
        if total_bytes + content_bytes > MAX_TOTAL_BYTES:
            get_console().print(
                "  [yellow]⚠[/yellow] Size limit reached, skipping remaining files"
            )
            break

        rel = str(file_path.relative_to(repo_path))
        suffix = file_path.suffix.lstrip(".")
        if suffix in SOURCE_EXTENSIONS:
            # Wrap source files in code blocks
            sections.append((rel, f"```{suffix}\n{content}\n```"))
        else:
            sections.append((rel, content))

        total_bytes += content_bytes
        get_console().print(f"  [green]✓[/green] {rel}")

    # Write combined document
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(f"# {repo_name}\n\n")
        for heading, content in sections:
            f.write(f"## {heading}\n\n{content}\n\n")

    total_kb = output_path.stat().st_size / 1024
    get_console().print(
        f"[bold green]Collected[/bold green] {len(sections)} files ({total_kb:.1f} KB) → {output_path}"
    )
    return output_path


def render_to_pdf(md_path: Path) -> Path:
    """Render a markdown file to PDF with Mermaid diagrams and tables.

    Uses md2pdf-mermaid's HTML pipeline (Chromium via Playwright) to produce
    a fully rendered PDF with diagrams as images.

    Args:
        md_path: Path to the markdown file.

    Returns:
        Path to the generated PDF file.
    """
    try:
        from md2pdf import convert_markdown_to_pdf_html
    except ImportError as exc:
        raise SystemExit(
            "PDF rendering requires md2pdf-mermaid with Playwright.\n"
            "Install Chromium: playwright install chromium"
        ) from exc

    pdf_path = md_path.with_suffix(".pdf")
    content = md_path.read_text(encoding="utf-8")
    title = md_path.stem.replace("_", " ").title()

    get_console().print("[blue]⏳[/blue] Rendering markdown to PDF (with Mermaid diagrams)...")
    convert_markdown_to_pdf_html(
        content,
        str(pdf_path),
        title=title,
        page_size="A4",
        orientation="portrait",
        enable_mermaid=True,
    )
    pdf_kb = pdf_path.stat().st_size / 1024
    get_console().print(f"[green]✓[/green] Rendered PDF ({pdf_kb:.1f} KB) → {pdf_path}")
    return pdf_path
