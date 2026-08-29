"""Command-line entry point and application event loop."""

import argparse
from collections.abc import Callable
from pathlib import Path

import FreeSimpleGUI as sg

from .files.bookshelf import scan_bookshelf
from .ui.reader_view import ComicStrip
from .ui.window import (
    ask_for_bookshelf,
    build_reader_window,
    desktop_size,
    maximize,
    TOP_BAR_TOGGLE_KEY,
    toggle_collapsible_group,
    toggle_top_bar,
    windowed_size,
)


def read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display a folder of images as a vertically scrolling comic."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help="folder containing comic images; omit it to use the folder picker",
    )
    parser.add_argument(
        "--windowed",
        "--no-maximize",
        "--not-maximized",
        action="store_false",
        dest="start_maximized",
        help="open in a normal window instead of starting maximized",
    )
    parser.add_argument(
        "--dual-page",
        action="store_true",
        help="start with paired pages; the first page remains by itself",
    )
    parser.add_argument(
        "--manga",
        "--manga-reading",
        action="store_true",
        dest="manga_reading",
        help="start with right-to-left ordering for paired pages",
    )
    return parser.parse_args(argv)


def _open_another_folder(reader: ComicStrip) -> None:
    reader.stop_zooming()
    selected = ask_for_bookshelf(reader.folder, reader.window.TKroot)
    if selected is None:
        return
    pages = scan_bookshelf(selected) if selected.is_dir() else []
    if pages:
        reader.open_bookshelf(pages, selected)
    else:
        sg.popup_error(f"No readable supported images were found in:\n{selected}")


def run_reader(
    folder: Path,
    *,
    start_maximized: bool = True,
    dual_page: bool = False,
    manga_reading: bool = False,
) -> int:
    if not folder.is_dir():
        sg.popup_error(f"This is not a folder:\n{folder}")
        return 2
    pages = scan_bookshelf(folder)
    if not pages:
        sg.popup_error(f"No readable supported images were found in:\n{folder}")
        return 1

    screen_width, screen_height = desktop_size()
    window = build_reader_window(
        windowed_size(screen_width, screen_height),
        dual_page=dual_page,
        manga_reading=manga_reading,
    )
    try:
        # Establish and paint a useful normal-window geometry first. Besides
        # making page one visible immediately, this gives the window manager a
        # real geometry to restore when the user leaves the maximized state.
        window.refresh()
        reader = ComicStrip(
            window,
            pages,
            folder,
            screen_width,
            dual_page=dual_page,
            manga_reading=manga_reading,
        )
        window.refresh()
        if start_maximized:
            maximize(window)
            window.refresh()
        actions: dict[str, Callable[[], None]] = {
            "-OPEN-": lambda: _open_another_folder(reader),
            "-ZOOM-OUT-": lambda: reader.zoom(-1),
            "-ZOOM-IN-": lambda: reader.zoom(1),
            "-FIT-": reader.fit_width,
            "-ORIGINAL-SIZE-": reader.show_original_size,
            "-FULLSCREEN-": reader.toggle_fullscreen,
        }
        while not reader.should_close:
            event, values = window.read(timeout=50)
            if event == sg.WIN_CLOSED:
                break
            if toggle_collapsible_group(window, event):
                continue
            if event == TOP_BAR_TOGGLE_KEY:
                toggle_top_bar(window)
                continue
            if event in {"-LIMIT-NATIVE-", "-LIMIT-FIT-"}:
                reader.set_zoom_limits(
                    prevent_image_upscale=bool(values["-LIMIT-NATIVE-"]),
                    stop_at_fit_width=bool(values["-LIMIT-FIT-"]),
                )
                continue
            if event in {"-DUAL-PAGE-", "-MANGA-READING-"}:
                reader.set_page_layout(
                    dual_page=bool(values["-DUAL-PAGE-"]),
                    manga_reading=bool(values["-MANGA-READING-"]),
                )
                continue
            action = actions.get(event)
            if action is not None:
                action()
    finally:
        window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = read_arguments(argv)
    folder = arguments.folder.expanduser() if arguments.folder else ask_for_bookshelf()
    return (
        0
        if folder is None
        else run_reader(
            folder,
            start_maximized=arguments.start_maximized,
            dual_page=arguments.dual_page,
            manga_reading=arguments.manga_reading,
        )
    )
