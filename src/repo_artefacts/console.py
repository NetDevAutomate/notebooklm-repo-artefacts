"""Shared Rich console for consistent output control.

All modules import get_console() from here instead of creating their own
Console() instances. This enables future --quiet/--verbose support via
configure_console().
"""

from __future__ import annotations

from rich.console import Console

_state: dict[str, Console] = {
    "console": Console(stderr=True),
}


def get_console() -> Console:
    """Return the shared console. Always call this, never cache across calls."""
    return _state["console"]


def configure_console(*, quiet: bool = False) -> None:
    """Swap the shared console. Called once from @app.callback() if needed."""
    _state["console"] = Console(stderr=True, quiet=quiet)
