"""Compact launcher shown when no comic folder is supplied."""

from pathlib import Path
import tkinter as tk

import FreeSimpleGUI as sg

from ..config import CONFIG_PATH, DEFAULT_CONFIG, load_config, save_config
from .window import APP_ICON_PATH, ask_for_bookshelf, compact_buttons


LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "csr_logo.png"
DROP_ZONE_KEY = "-LAUNCHER-DROP-ZONE-"
DROP_EVENT_KEY = "-LAUNCHER-FOLDER-DROPPED-"
SAVE_KEY = "-LAUNCHER-SAVE-"
LAUNCH_BLOCK_SIZE = (260, 260)
CONFIG_KEYS = {
    "prevent_image_upscale": "-LAUNCHER-LIMIT-NATIVE-",
    "stop_at_fit_width": "-LAUNCHER-LIMIT-FIT-",
    "dual_page": "-LAUNCHER-DUAL-PAGE-",
    "manga_reading": "-LAUNCHER-MANGA-READING-",
    "page_spacing": "-LAUNCHER-PAGE-SPACING-",
    "detect_double_spreads": "-LAUNCHER-DETECT-SPREADS-",
    "remember_folder": "-LAUNCHER-REMEMBER-FOLDER-",
}
CONFIG_LABELS = {
    "prevent_image_upscale": "Don't enlarge images",
    "stop_at_fit_width": "Stop at fit width",
    "dual_page": "Dual page",
    "manga_reading": "Manga order",
    "page_spacing": "Page spacing",
    "detect_double_spreads": "Detect double-page spreads",
    "remember_folder": "Remember folder",
}


def config_from_values(
    values: dict[str, object], current: dict[str, object] | None = None
) -> dict[str, object]:
    """Update launcher-editable settings while preserving hidden settings."""
    config = (current or DEFAULT_CONFIG).copy()
    for name, element_key in CONFIG_KEYS.items():
        config[name] = bool(values.get(element_key, config[name]))
    return config


def dropped_folder(data: object, root: object) -> Path | None:
    """Return the first dropped directory from Tk DnD event data."""
    try:
        entries = root.tk.splitlist(str(data))
    except (AttributeError, TypeError, ValueError):
        return None
    for entry in entries:
        candidate = Path(entry).expanduser()
        if candidate.is_dir():
            return candidate
    return None


def _enable_folder_drop(window: sg.Window) -> bool:
    """Register the launch button as an operating-system folder drop target."""
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD

        TkinterDnD._require(window.TKroot)
        widget = window[DROP_ZONE_KEY].Widget
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind(
            "<<Drop>>",
            lambda event: window.write_event_value(DROP_EVENT_KEY, event.data),
        )
    except (ImportError, RuntimeError, TypeError, tk.TclError):
        return False
    return True


def build_launcher_window(config: dict[str, object]) -> sg.Window:
    """Construct the logo, folder target, and basic configuration controls."""
    sg.theme("DarkGrey13")
    checkboxes = [
        sg.Checkbox(
            CONFIG_LABELS[name],
            default=bool(config[name]),
            key=CONFIG_KEYS[name],
            pad=((8, 8), (4, 4)),
        )
        for name in CONFIG_KEYS
    ]
    layout = [
        [
            sg.Push(),
            sg.Column(
                [[sg.Image(filename=str(LOGO_PATH), subsample=5, pad=(0, 0))]],
                size=LAUNCH_BLOCK_SIZE,
                element_justification="center",
                vertical_alignment="center",
                pad=((12, 6), (12, 8)),
            ),
            sg.Column(
                [[sg.Button(
                    "Drag and drop here\nto open\n\nClick to browse",
                    key=DROP_ZONE_KEY,
                    size=(26, 12),
                    font=("TkDefaultFont", 12, "bold"),
                    button_color=("#f3f7ff", "#263b69"),
                    border_width=3,
                    pad=(0, 0),
                    tooltip="Drop a comic folder or click to choose one",
                )]],
                size=LAUNCH_BLOCK_SIZE,
                element_justification="center",
                vertical_alignment="center",
                pad=((6, 12), (12, 8)),
            ),
            sg.Push(),
        ],
        [
            sg.Frame(
                "Basic settings",
                [
                    checkboxes[:3],
                    checkboxes[3:],
                    [
                        sg.Text(
                            f"Saved in {CONFIG_PATH}",
                            text_color="#aebbd4",
                        ),
                        sg.Button("Save settings", key=SAVE_KEY),
                    ],
                ],
                title_location=sg.TITLE_LOCATION_TOP,
                element_justification="center",
                expand_x=True,
                pad=(12, (4, 12)),
            )
        ],
        [
            sg.Text(
                "Developed by Alef-0  •  Vibecoded using GPT5.6 - Codex",
                expand_x=True,
                justification="center",
            )
        ],
        [
            sg.Text(
                "MIT License",
                expand_x=True,
                justification="center",
                pad=((0, 0), (0, 10)),
            )
        ],
    ]
    window = sg.Window(
        "Comic and Scroll Reader",
        layout,
        icon=str(APP_ICON_PATH),
        finalize=True,
        resizable=False,
        element_justification="center",
        use_default_focus=False,
    )
    compact_buttons(window, {DROP_ZONE_KEY})
    return window


def run_launcher() -> Path | None:
    """Run the launcher and return the folder chosen by the user."""
    config = load_config()
    window = build_launcher_window(config)
    drop_enabled = _enable_folder_drop(window)
    if not drop_enabled:
        window[DROP_ZONE_KEY].update("Click here\nto choose a folder")
    try:
        while True:
            event, values = window.read()
            if event == sg.WIN_CLOSED:
                return None
            if event == SAVE_KEY:
                try:
                    save_config(config_from_values(values, config))
                except OSError as error:
                    sg.popup_error(f"Unable to save settings:\n{error}")
                else:
                    sg.popup_ok(f"Settings saved to:\n{CONFIG_PATH}")
                continue
            if event == DROP_EVENT_KEY:
                selected = dropped_folder(values.get(DROP_EVENT_KEY), window.TKroot)
                if selected is None:
                    sg.popup_error("Drop a folder containing comic pages.")
                    continue
                return selected
            if event == DROP_ZONE_KEY:
                try:
                    selected = ask_for_bookshelf(parent=window.TKroot)
                except RuntimeError as error:
                    sg.popup_error(f"Unable to open the system folder chooser:\n{error}")
                    continue
                if selected is not None:
                    return selected
    finally:
        window.close()
