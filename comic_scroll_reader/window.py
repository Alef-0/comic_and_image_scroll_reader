"""Window construction and native folder-dialog helpers."""

from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import FreeSimpleGUI as sg


FALLBACK_DESKTOP_SIZE = (1280, 720)
CANVAS_COLOR = "#1c1c1c"
UI_FONT = ("TkDefaultFont", 10, "bold")


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


def build_reader_window() -> sg.Window:
    sg.theme("DarkGrey13")
    sg.set_options(font=UI_FONT)
    controls = [
        sg.Button("Open Folder", key="-OPEN-"),
        sg.Button("−", key="-ZOOM-OUT-", tooltip="Zoom out (Ctrl+-)"),
        sg.Button("+", key="-ZOOM-IN-", tooltip="Zoom in (Ctrl++)"),
        sg.Button("Fit Width", key="-FIT-"),
        sg.Button("Fullscreen", key="-FULLSCREEN-", tooltip="Toggle fullscreen (F11)"),
        sg.Text("", key="-STATUS-", expand_x=True, justification="right"),
    ]
    layout = [
        controls,
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
    return sg.Window(
        "Comic and Image Scroll Reader",
        layout,
        margins=(0, 0),
        resizable=True,
        finalize=True,
        use_default_focus=False,
    )


def maximize(window: sg.Window) -> None:
    """Maximize without FreeSimpleGUI's Linux fullscreen fallback."""
    try:
        window.TKroot.attributes("-zoomed", True)
    except tk.TclError:
        window.TKroot.state("zoomed")
