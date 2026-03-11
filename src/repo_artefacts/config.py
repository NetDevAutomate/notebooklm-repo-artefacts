"""User configuration for repo-artefacts."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "repo-artefacts"
CONFIG_FILE = CONFIG_DIR / "config.toml"


@dataclass
class Config:
    """Repo-artefacts configuration."""

    default_store: str | None = None
    store_cache_dir: Path = field(
        default_factory=lambda: Path.home() / ".cache" / "repo-artefacts" / "stores"
    )


def load_config() -> Config:
    """Load config from ~/.config/repo-artefacts/config.toml.

    Returns default Config if file doesn't exist or is invalid.
    """
    if not CONFIG_FILE.exists():
        return Config()
    try:
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
        return Config(
            default_store=data.get("default_store"),
            store_cache_dir=Path(data["store_cache_dir"])
            if "store_cache_dir" in data
            else Config().store_cache_dir,
        )
    except (tomllib.TOMLDecodeError, KeyError, TypeError):
        return Config()


def save_config(config: Config) -> None:
    """Save config to TOML file. Creates directory if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if config.default_store:
        lines.append(f'default_store = "{config.default_store}"')
    if config.store_cache_dir != Config().store_cache_dir:
        lines.append(f'store_cache_dir = "{config.store_cache_dir}"')
    CONFIG_FILE.write_text("\n".join(lines) + "\n")
