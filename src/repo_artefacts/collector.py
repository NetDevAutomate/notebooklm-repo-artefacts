"""Collect key files from a git repository into a single markdown document."""

from pathlib import Path

from rich.console import Console

console = Console()

MAX_TOTAL_BYTES = 500 * 1024  # 500KB
MAX_SOURCE_LINES = 500
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
SKIP_DIRS = {
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


def _find_file(repo_path: Path, names: list[str]) -> Path | None:
    """Return the first matching file from a list of candidate names."""
    for name in names:
        path = repo_path / name
        if path.is_file():
            return path
    return None


def _iter_files(directory: Path, extensions: set[str] | None = None) -> list[Path]:
    """Recursively find files, skipping ignored directories."""
    results: list[Path] = []
    if not directory.is_dir():
        return results
    for item in sorted(directory.rglob("*")):
        if any(skip in item.parts for skip in SKIP_DIRS):
            continue
        if item.is_file():
            if extensions is None or item.suffix in extensions:
                results.append(item)
    return results


def _read_safe(path: Path, max_lines: int | None = None) -> str | None:
    """Read a file, returning None if it fails or exceeds line limit."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if max_lines and text.count("\n") > max_lines:
            return None
        return text
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
        click.BadParameter: If repo_path doesn't exist or isn't a directory.
    """
    import click

    if not repo_path.is_dir():
        raise click.BadParameter(f"'{repo_path}' is not a directory")

    sections: list[tuple[str, str]] = []  # (heading, content)
    repo_name = repo_path.resolve().name

    # 1. README
    readme = _find_file(repo_path, README_NAMES)
    if readme:
        content = _read_safe(readme)
        if content:
            sections.append((readme.name, content))
            console.print(f"  [green]✓[/green] {readme.name}")

    # 2. Docs directory
    docs_dir = repo_path / "docs"
    if docs_dir.is_dir():
        for md_file in _iter_files(docs_dir, {".md", ".rst", ".txt"}):
            content = _read_safe(md_file)
            if content:
                rel = md_file.relative_to(repo_path)
                sections.append((str(rel), content))
                console.print(f"  [green]✓[/green] {rel}")

    # 3. Project config
    config = _find_file(repo_path, CONFIG_FILES)
    if config:
        content = _read_safe(config)
        if content:
            rel = config.relative_to(repo_path)
            sections.append((str(rel), content))
            console.print(f"  [green]✓[/green] {rel}")

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
                console.print(
                    "  [yellow]⚠[/yellow] Size limit reached, skipping remaining source files"
                )
                break
            rel = src_file.relative_to(repo_path)
            suffix = src_file.suffix.lstrip(".")
            sections.append((str(rel), f"```{suffix}\n{content}\n```"))
            source_used += len(content)
            console.print(f"  [green]✓[/green] {rel}")

    # Write combined document
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(f"# {repo_name}\n\n")
        for heading, content in sections:
            f.write(f"## {heading}\n\n{content}\n\n")

    total_kb = output_path.stat().st_size / 1024
    console.print(
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
    from md2pdf import convert_markdown_to_pdf_html

    pdf_path = md_path.with_suffix(".pdf")
    content = md_path.read_text(encoding="utf-8")
    title = md_path.stem.replace("_", " ").title()

    console.print(
        "[blue]⏳[/blue] Rendering markdown to PDF (with Mermaid diagrams)..."
    )
    convert_markdown_to_pdf_html(
        content,
        str(pdf_path),
        title=title,
        page_size="A4",
        orientation="portrait",
        enable_mermaid=True,
    )
    pdf_kb = pdf_path.stat().st_size / 1024
    console.print(f"[green]✓[/green] Rendered PDF ({pdf_kb:.1f} KB) → {pdf_path}")
    return pdf_path
