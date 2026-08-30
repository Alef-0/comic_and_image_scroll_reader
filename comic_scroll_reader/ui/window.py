"""Window construction and native folder-dialog helpers."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import FreeSimpleGUI as sg


FALLBACK_DESKTOP_SIZE = (1280, 720)
WINDOWED_SIZE_RATIO = (0.75, 0.80)
CANVAS_COLOR = "#1c1c1c"
UI_FONT = ("TkDefaultFont", 10, "bold")
GROUP_HEADER_FONT = ("TkDefaultFont", 9, "bold")
TOP_BAR_TOGGLE_FONT = ("TkDefaultFont", 5, "bold")
TOP_BAR_TOGGLE_HEIGHT = 1
GROUP_TOGGLE_SUFFIX = "::toggle"
TOP_BAR_KEY = "-TOP-BAR-"
TOP_BAR_TOGGLE_KEY = "-TOGGLE-TOP-BAR-"
PAGE_COUNTER_KEY = "-PAGE-COUNTER-"
PAGE_NUMBER_INPUT_KEY = "-PAGE-NUMBER-"
COLLAPSIBLE_GROUPS = {
    "-FILE-GROUP-": ("File", "-FILE-GROUP-CONTENT-"),
    "-IMAGE-SIZE-GROUP-": ("Image size", "-IMAGE-SIZE-GROUP-CONTENT-"),
    "-PAGE-LAYOUT-GROUP-": ("Page layout", "-PAGE-LAYOUT-GROUP-CONTENT-"),
    "-WINDOW-GROUP-": ("Window", "-WINDOW-GROUP-CONTENT-"),
}


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
    """Ask for a page folder without displaying dot-prefixed directories."""
    owns_root = parent is None
    dialog_root = tk.Tk() if owns_root else parent
    if owns_root:
        dialog_root.withdraw()
    try:
        # Linux loads this dialog lazily. Loading it first stops Tk from
        # resetting the hidden-folder preference a moment later.
        dialog_root.tk.call("auto_load", "::tk::dialog::file::Update")
        dialog_root.tk.setvar("::tk::dialog::file::showHiddenVar", False)
        dialog_root.tk.setvar("::tk::dialog::file::showHiddenBtn", True)
        selected = filedialog.askdirectory(
            parent=dialog_root,
            title="Choose a folder containing comic pages",
            initialdir=str(starting_at) if starting_at else None,
            mustexist=True,
        )
    finally:
        if owns_root:
            dialog_root.destroy()
    return Path(selected).expanduser() if selected else None


def windowed_size(desktop_width: int, desktop_height: int) -> tuple[int, int]:
    """Return a useful initial size while preserving room around the window."""
    width_ratio, height_ratio = WINDOWED_SIZE_RATIO
    return (
        max(1, round(desktop_width * width_ratio)),
        max(1, round(desktop_height * height_ratio)),
    )


def _collapsible_group(title: str, content: list[list[sg.Element]], key: str) -> sg.Frame:
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
                        visible=False,
                        pad=(0, 0),
                        metadata={"expanded": False},
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
        header = tk.Button(
            labelframe,
            text=f"{title} ▸",
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
) -> sg.Window:
    sg.theme("DarkGrey13")
    sg.set_options(font=UI_FONT)
    controls = [
        _collapsible_group(
            "File", [[sg.Button("Open Folder", key="-OPEN-")]], "-FILE-GROUP-"
        ),
        _collapsible_group(
            "Image size",
            [
                [
                    sg.Button("−", key="-ZOOM-OUT-", tooltip="Zoom out (Ctrl+-)"),
                    sg.Button("+", key="-ZOOM-IN-", tooltip="Zoom in (Ctrl++)"),
                    sg.Button("Fit Width", key="-FIT-"),
                    sg.Button("Original Size", key="-ORIGINAL-SIZE-"),
                    sg.Checkbox(
                        "Don't enlarge images",
                        default=False,
                        key="-LIMIT-NATIVE-",
                        enable_events=True,
                    ),
                    sg.Checkbox(
                        "Stop at fit width",
                        default=True,
                        key="-LIMIT-FIT-",
                        enable_events=True,
                    ),
                ],
            ],
            "-IMAGE-SIZE-GROUP-",
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
                        "Page borders",
                        default=True,
                        key="-PAGE-BORDERS-",
                        enable_events=True,
                        tooltip="Show white separators between pages",
                    ),
                ]
            ],
            "-PAGE-LAYOUT-GROUP-",
        ),
        _collapsible_group(
            "Window",
            [[sg.Button("Fullscreen", key="-FULLSCREEN-", tooltip="Toggle fullscreen (F11)")]],
            "-WINDOW-GROUP-",
        ),
        sg.Text("", key="-STATUS-", expand_x=True, justification="right"),
        sg.Text(
            "1 / 1",
            key=PAGE_COUNTER_KEY,
            tooltip="Go to page",
            enable_events=True,
            pad=((6, 6), (0, 0)),
        ),
    ]
    layout = [
        [
            sg.pin(
                sg.Column(
                    [controls],
                    key=TOP_BAR_KEY,
                    expand_x=True,
                    pad=(0, 0),
                    metadata={"visible": True},
                ),
                expand_x=True,
            )
        ],
        [
            sg.Button(
                "▲",
                key=TOP_BAR_TOGGLE_KEY,
                tooltip="Collapse top bar",
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
        "Comic and Image Scroll Reader",
        layout,
        margins=(0, 0),
        resizable=True,
        size=size,
        finalize=True,
        use_default_focus=False,
    )
    _install_clickable_group_headers(window)
    window[PAGE_COUNTER_KEY].Widget.configure(cursor="hand2")
    window[TOP_BAR_TOGGLE_KEY].Widget.configure(pady=0, highlightthickness=0)
    return window


def maximize(window: sg.Window) -> None:
    """Maximize without FreeSimpleGUI's Linux fullscreen fallback."""
    try:
        window.TKroot.attributes("-zoomed", True)
    except tk.TclError:
        window.TKroot.state("zoomed")
