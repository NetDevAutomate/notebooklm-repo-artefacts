"""Collect key files from a git repository into a single markdown document."""

from __future__ import annotations

import os
from pathlib import Path

from repo_artefacts.console import get_console
from repo_artefacts.exceptions import CollectionError

MAX_TOTAL_BYTES = 500 * 1024  # 500KB
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
SKIP_DIRS = frozenset(
    {
        ".git",
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
    }
)
README_NAMES = ["README.md", "README.rst", "README.txt", "README"]
CONFIG_FILES = [
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
]


def _is_git_repo(path: Path) -> bool:
    """Check if path is a git repo root (handles regular repos, worktrees, bare repos)."""
    git_path = path / ".git"
    # Regular repo: .git is a directory
    # Worktree: .git is a file containing 'gitdir: /path/to/...'
    if git_path.is_dir() or git_path.is_file():
        return True
    # Bare repo: has HEAD and objects/ directly
    return (path / "HEAD").is_file() and (path / "objects").is_dir()


def _find_file(repo_path: Path, names: list[str]) -> Path | None:
    """Return the first matching file from a list of candidate names."""
    for name in names:
        path = repo_path / name
        if path.is_file():
            return path
    return None


def _iter_files(directory: Path, extensions: set[str] | None = None) -> list[Path]:
    """Walk directory tree with pruning. Explicit followlinks=False."""
    results: list[Path] = []
    if not directory.is_dir():
        return results
    for root_str, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        root_path = Path(root_str)
        for fname in sorted(files):
            if extensions is None or Path(fname).suffix in extensions:
                results.append(root_path / fname)
    return results


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


def collect_repo_content(repo_path: Path, output_path: Path) -> Path:
    """Collect key files from a git repo into a single markdown document.

    Prioritises README and docs, then config, then source files.
    Truncates source files if total content exceeds 500KB.

    Args:
        repo_path: Path to the git repository root.
        output_path: Path to write the combined markdown file.

    Returns:
        The output_path written to.

    Raises:
        ValueError: If repo_path doesn't exist, isn't a directory, or isn't a git repo.
    """
    if not repo_path.is_dir():
        raise CollectionError(f"'{repo_path}' is not a directory")
    if not _is_git_repo(repo_path):
        raise CollectionError(f"'{repo_path}' is not a git repository")

    sections: list[tuple[str, str]] = []  # (heading, content)
    repo_name = repo_path.resolve().name

    # 1. README
    readme = _find_file(repo_path, README_NAMES)
    if readme:
        content = _read_safe(readme)
        if content:
            sections.append((readme.name, content))
            get_console().print(f"  [green]✓[/green] {readme.name}")

    # 2. Docs directory
    docs_dir = repo_path / "docs"
    if docs_dir.is_dir():
        for md_file in _iter_files(docs_dir, {".md", ".rst", ".txt"}):
            content = _read_safe(md_file)
            if content:
                rel = md_file.relative_to(repo_path)
                sections.append((str(rel), content))
                get_console().print(f"  [green]✓[/green] {rel}")

    # 3. Project config
    config = _find_file(repo_path, CONFIG_FILES)
    if config:
        content = _read_safe(config)
        if content:
            rel = config.relative_to(repo_path)
            sections.append((str(rel), content))
            get_console().print(f"  [green]✓[/green] {rel}")

    # 4. Source files (budget-aware)
    priority_total = sum(len(c) for _, c in sections)
    source_budget = MAX_TOTAL_BYTES - priority_total

    if source_budget > 0:
        src_dir = repo_path / "src"
        search_dirs = [src_dir] if src_dir.is_dir() else [repo_path]
        source_files = []
        for d in search_dirs:
            source_files.extend(_iter_files(d, SOURCE_EXTENSIONS))

        source_used = 0
        for src_file in source_files:
            content = _read_safe(src_file, max_lines=MAX_SOURCE_LINES)
            if content is None:
                continue
            if source_used + len(content) > source_budget:
                get_console().print(
                    "  [yellow]⚠[/yellow] Size limit reached, skipping remaining source files"
                )
                break
            rel = src_file.relative_to(repo_path)
            suffix = src_file.suffix.lstrip(".")
            sections.append((str(rel), f"```{suffix}\n{content}\n```"))
            source_used += len(content)
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
