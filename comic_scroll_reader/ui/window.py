"""Window construction and desktop-native folder-dialog helpers."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tkinter as tk

import FreeSimpleGUI as sg


FALLBACK_DESKTOP_SIZE = (1280, 720)
WINDOWED_SIZE_RATIO = (0.75, 0.80)
CANVAS_COLOR = "#1c1c1c"
UI_FONT = ("TkDefaultFont", 11, "bold")
GROUP_HEADER_FONT = ("TkDefaultFont", 10, "bold")
PAGE_COUNTER_FONT = ("TkDefaultFont", 11, "bold underline")
CLICKABLE_TEXT_FONT = ("TkDefaultFont", 11, "bold underline")
TOP_BAR_TOGGLE_FONT = ("TkDefaultFont", 5, "bold")
TOP_BAR_TOGGLE_HEIGHT = 1
GROUP_TOGGLE_SUFFIX = "::toggle"
TOP_BAR_KEY = "-TOP-BAR-"
TOP_BAR_TOGGLE_KEY = "-TOGGLE-TOP-BAR-"
PAGE_COUNTER_KEY = "-PAGE-COUNTER-"
ZOOM_STATUS_KEY = "-ZOOM-STATUS-"
ZOOM_OUT_KEY = "-ZOOM-OUT-"
ZOOM_IN_KEY = "-ZOOM-IN-"
PAGE_NUMBER_INPUT_KEY = "-PAGE-NUMBER-"
COLLAPSIBLE_GROUPS = {
    "-IMAGE-SIZE-GROUP-": ("Image size", "-IMAGE-SIZE-GROUP-CONTENT-"),
    "-PAGE-LAYOUT-GROUP-": ("Page layout", "-PAGE-LAYOUT-GROUP-CONTENT-"),
    "-EXPERIMENTAL-GROUP-": ("Experimental", "-EXPERIMENTAL-GROUP-CONTENT-"),
}
BUTTON_PAD = ((2, 2), (1, 1))


def desktop_size() -> tuple[int, int]:
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        root.withdraw()
        width = int(root.winfo_screenwidth())
        height = int(root.winfo_screenheight())
        if width > 0 and height > 0:
            return width, height
    except tk.TclError:
        pass
    finally:
        if root is not None:
            root.destroy()
    return FALLBACK_DESKTOP_SIZE


def ask_for_bookshelf(
    starting_at: Path | None = None, parent: tk.Misc | None = None
) -> Path | None:
    """Ask for a page folder through the current desktop's dialog helper."""
    del parent  # Native dialog helpers do not use Tk parent windows.
    start = (starting_at or Path.home()).expanduser().resolve()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()
    helpers = ["kdialog", "zenity"] if "kde" in desktop else ["zenity", "kdialog"]

    for helper in helpers:
        if shutil.which(helper) is None:
            continue
        if helper == "kdialog":
            command = [
                helper,
                "--getexistingdirectory",
                str(start),
                "--title",
                "Choose a folder containing comic pages",
            ]
        else:
            command = [
                helper,
                "--file-selection",
                "--directory",
                "--title=Choose a folder containing comic pages",
                f"--filename={start}/",
            ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            detail = result.stderr.strip() or f"{helper} exited unexpectedly."
            raise RuntimeError(detail)
        selected = result.stdout.strip()
        return Path(selected).expanduser() if selected else None

    raise RuntimeError(
        "No desktop folder chooser was found. Install Zenity or KDialog."
    )


def windowed_size(desktop_width: int, desktop_height: int) -> tuple[int, int]:
    """Return a useful initial size while preserving room around the window."""
    width_ratio, height_ratio = WINDOWED_SIZE_RATIO
    return (
        max(1, round(desktop_width * width_ratio)),
        max(1, round(desktop_height * height_ratio)),
    )


def size_from_geometry(geometry: object) -> tuple[int, int] | None:
    """Extract a usable width and height from Tk's saved geometry string."""
    if not isinstance(geometry, str):
        return None
    match = re.fullmatch(r"(\d+)x(\d+)[+-]\d+[+-]\d+", geometry)
    if match is None:
        return None
    width, height = (int(value) for value in match.groups())
    return (width, height) if width > 0 and height > 0 else None


def is_maximized(window: sg.Window) -> bool:
    """Return whether the desktop window manager reports a maximized window."""
    try:
        return bool(window.TKroot.attributes("-zoomed"))
    except tk.TclError:
        return window.TKroot.state() == "zoomed"


def reader_window_title(folder: Path) -> str:
    """Return the reader title with the active folder clearly identified."""
    return f"Comic and Scroll Reader — {folder.name or folder}"


def _collapsible_group(
    title: str,
    content: list[list[sg.Element]],
    key: str,
    *,
    expanded: bool = False,
) -> sg.Frame:
    """Create a group whose compact title remains visible while collapsed."""
    content_key = COLLAPSIBLE_GROUPS[key][1]
    return sg.Frame(
        "",
        [
            [
                sg.pin(
                    sg.Column(
                        content,
                        key=content_key,
                        visible=expanded,
                        pad=(0, 0),
                        metadata={"expanded": expanded},
                    )
                )
            ]
        ],
        key=key,
        tooltip="Click the group title to expand or collapse",
        metadata={"header_widget": None},
    )


def _install_clickable_group_headers(window: sg.Window) -> None:
    """Use real Tk buttons as labelframe titles so every click is repeatable."""
    for frame_key, (title, _content_key) in COLLAPSIBLE_GROUPS.items():
        frame = window[frame_key]
        labelframe: tk.LabelFrame = frame.Widget
        background = labelframe.cget("background")
        expanded = bool(window[_content_key].metadata["expanded"])
        header = tk.Button(
            labelframe,
            text=f"{title} {'▾' if expanded else '▸'}",
            command=lambda key=frame_key: window.write_event_value(
                f"{key}{GROUP_TOGGLE_SUFFIX}", None
            ),
            background=background,
            foreground=labelframe.cget("foreground"),
            activebackground=background,
            activeforeground=labelframe.cget("foreground"),
            font=GROUP_HEADER_FONT,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            padx=2,
            pady=0,
            cursor="hand2",
        )
        labelframe.configure(labelwidget=header)
        frame.metadata["header_widget"] = header


def toggle_collapsible_group(window: sg.Window, event: object) -> bool:
    """Toggle the group represented by a clickable-header event, if any."""
    for frame_key, (title, content_key) in COLLAPSIBLE_GROUPS.items():
        if event != f"{frame_key}{GROUP_TOGGLE_SUFFIX}":
            continue
        content = window[content_key]
        expanded = not bool(content.metadata["expanded"])
        content.metadata["expanded"] = expanded
        content.update(visible=expanded)
        header = window[frame_key].metadata["header_widget"]
        header.configure(text=f"{title} {'▾' if expanded else '▸'}")
        return True
    return False


def toggle_top_bar(window: sg.Window) -> None:
    """Hide or restore the controls while leaving their full-width toggle visible."""
    top_bar = window[TOP_BAR_KEY]
    visible = not bool(top_bar.metadata["visible"])
    top_bar.metadata["visible"] = visible
    top_bar.update(visible=visible)
    toggle = window[TOP_BAR_TOGGLE_KEY]
    toggle.update(text="▲" if visible else "▼")
    toggle.set_tooltip("Collapse top bar" if visible else "Expand top bar")


def ask_for_page_number(current: int, total: int) -> str | None:
    """Display a compact page-jump dialog with consistently sized controls."""
    layout = [
        [
            sg.Text(
                f"Enter a page number (1-{total}):",
                expand_x=True,
                justification="center",
            )
        ],
        [
            sg.Input(
                str(current),
                key=PAGE_NUMBER_INPUT_KEY,
                justification="center",
                focus=True,
                expand_x=True,
            )
        ],
        [
            sg.Push(),
            sg.Button("Cancel", key="-CANCEL-PAGE-JUMP-", size=(10, 1)),
            sg.Button(
                "OK",
                key="-CONFIRM-PAGE-JUMP-",
                size=(10, 1),
                bind_return_key=True,
            ),
            sg.Push(),
        ],
    ]
    dialog = sg.Window(
        "Go to page",
        layout,
        modal=True,
        keep_on_top=True,
        finalize=True,
        element_justification="center",
    )
    try:
        event, values = dialog.read()
        if event != "-CONFIRM-PAGE-JUMP-":
            return None
        return str(values[PAGE_NUMBER_INPUT_KEY])
    finally:
        dialog.close()


def build_reader_window(
    size: tuple[int, int] | None = None,
    *,
    dual_page: bool = False,
    manga_reading: bool = False,
    page_spacing: bool = True,
    detect_double_spreads: bool = True,
    prevent_image_upscale: bool = False,
    stop_at_fit_width: bool = True,
    top_bar_visible: bool = True,
    expand_all: bool = False,
    title: str = "Comic and Scroll Reader",
) -> sg.Window:
    sg.theme("DarkGrey13")
    sg.set_options(font=UI_FONT)
    controls = [
        sg.Button("Open Folder", key="-OPEN-", pad=BUTTON_PAD),
        sg.Button("Save Configs", key="-SAVE-CONFIGS-", pad=BUTTON_PAD),
        _collapsible_group(
            "Image size",
            [
                [
                    sg.Button("Fit Width", key="-FIT-", pad=BUTTON_PAD),
                    sg.Button("Original Size", key="-ORIGINAL-SIZE-", pad=BUTTON_PAD),
                    sg.Checkbox(
                        "Don't enlarge images",
                        default=prevent_image_upscale,
                        key="-LIMIT-NATIVE-",
                        enable_events=True,
                    ),
                    sg.Checkbox(
                        "Stop at fit width",
                        default=stop_at_fit_width,
                        key="-LIMIT-FIT-",
                        enable_events=True,
                    ),
                ],
            ],
            "-IMAGE-SIZE-GROUP-",
            expanded=expand_all,
        ),
        _collapsible_group(
            "Page layout",
            [
                [
                    sg.Checkbox(
                        "Dual page",
                        default=dual_page,
                        key="-DUAL-PAGE-",
                        enable_events=True,
                    ),
                    sg.Checkbox(
                        "Manga order",
                        default=manga_reading,
                        key="-MANGA-READING-",
                        enable_events=True,
                        tooltip="Show paired pages from right to left",
                    ),
                    sg.Checkbox(
                        "Page spacing",
                        default=page_spacing,
                        key="-PAGE-SPACING-",
                        enable_events=True,
                        tooltip="Leave background-colored space between pages",
                    ),
                ]
            ],
            "-PAGE-LAYOUT-GROUP-",
            expanded=expand_all,
        ),
        _collapsible_group(
            "Experimental",
            [
                [
                    sg.Checkbox(
                        "Detect double-page spreads",
                        default=detect_double_spreads,
                        key="-DETECT-DOUBLE-SPREADS-",
                        enable_events=True,
                        tooltip=(
                            "Detect unusually wide images and keep them in solo rows"
                        ),
                    )
                ]
            ],
            "-EXPERIMENTAL-GROUP-",
            expanded=expand_all,
        ),
        sg.Text("", key="-STATUS-", expand_x=True, justification="right"),
        sg.Text(
            "−",
            key=ZOOM_OUT_KEY,
            font=CLICKABLE_TEXT_FONT,
            tooltip="Zoom out (-)",
            enable_events=True,
            pad=((5, 2), (0, 0)),
        ),
        sg.Text("75%", key=ZOOM_STATUS_KEY, pad=((2, 2), (0, 0))),
        sg.Text(
            "+",
            key=ZOOM_IN_KEY,
            font=CLICKABLE_TEXT_FONT,
            tooltip="Zoom in (+)",
            enable_events=True,
            pad=((2, 5), (0, 0)),
        ),
        sg.Text(
            "Pages 1 / 1",
            key=PAGE_COUNTER_KEY,
            font=PAGE_COUNTER_FONT,
            tooltip="Go to page",
            enable_events=True,
            pad=((6, 6), (0, 0)),
        ),
    ]
    control_rows = (
        [
            [*controls[:3], sg.Push(), *controls[5:]],
            [controls[3], controls[4]],
        ]
        if expand_all
        else [controls]
    )
    layout = [
        [
            sg.pin(
                sg.Column(
                    control_rows,
                    key=TOP_BAR_KEY,
                    expand_x=True,
                    pad=(0, 0),
                    visible=top_bar_visible,
                    metadata={"visible": top_bar_visible},
                ),
                expand_x=True,
            )
        ],
        [
            sg.Button(
                "▲" if top_bar_visible else "▼",
                key=TOP_BAR_TOGGLE_KEY,
                tooltip="Collapse top bar" if top_bar_visible else "Expand top bar",
                expand_x=True,
                border_width=0,
                size=(None, TOP_BAR_TOGGLE_HEIGHT),
                font=TOP_BAR_TOGGLE_FONT,
                pad=(0, 0),
            )
        ],
        [
            sg.Canvas(
                key="-CANVAS-",
                background_color=CANVAS_COLOR,
                expand_x=True,
                expand_y=True,
                pad=(0, 0),
            )
        ],
    ]
    window = sg.Window(
        title,
        layout,
        margins=(0, 0),
        resizable=True,
        size=size,
        finalize=True,
        use_default_focus=False,
    )
    _install_clickable_group_headers(window)
    for key in (PAGE_COUNTER_KEY, ZOOM_OUT_KEY, ZOOM_IN_KEY):
        window[key].Widget.configure(cursor="hand2")
    window[TOP_BAR_TOGGLE_KEY].Widget.configure(pady=0, highlightthickness=0)
    return window


def maximize(window: sg.Window) -> None:
    """Maximize without FreeSimpleGUI's Linux fullscreen fallback."""
    try:
        window.TKroot.attributes("-zoomed", True)
    except tk.TclError:
        window.TKroot.state("zoomed")
