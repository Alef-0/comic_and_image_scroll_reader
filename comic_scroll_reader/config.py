"""Persistent reader preferences stored in the user's config directory."""

import json
import os
from pathlib import Path


CONFIG_DIRECTORY = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "comic-scroll-reader"
CONFIG_PATH = CONFIG_DIRECTORY / "reader_config.json"
DEFAULT_CONFIG = {
    "prevent_image_upscale": False,
    "stop_at_fit_width": True,
    "dual_page": False,
    "manga_reading": False,
    "page_spacing": True,
    "detect_double_spreads": True,
    "top_bar_visible": True,
}


def load_config(path: Path = CONFIG_PATH) -> dict[str, bool]:
    """Load known boolean preferences, falling back safely for invalid files."""
    config = DEFAULT_CONFIG.copy()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return config
    if not isinstance(saved, dict):
        return config
    for key, default in DEFAULT_CONFIG.items():
        value = saved.get(key, default)
        if isinstance(value, bool):
            config[key] = value
    return config


def save_config(config: dict[str, bool], path: Path = CONFIG_PATH) -> None:
    """Save only recognized boolean preferences in a stable JSON format."""
    saved = {
        key: config.get(key, default)
        if isinstance(config.get(key, default), bool)
        else default
        for key, default in DEFAULT_CONFIG.items()
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
