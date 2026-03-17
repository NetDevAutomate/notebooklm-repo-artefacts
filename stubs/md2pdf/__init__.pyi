"""Minimal stubs for md2pdf package."""

def convert_markdown_to_pdf(
    markdown_text: str,
    output_path: str,
    *,
    title: str = ...,
    page_size: str = ...,
    orientation: str = ...,
    enable_mermaid: bool = ...,
) -> None: ...
def convert_markdown_to_pdf_html(
    markdown_text: str,
    output_path: str,
    *,
    title: str = ...,
    page_size: str = ...,
    orientation: str = ...,
    enable_mermaid: bool = ...,
) -> None: ...
