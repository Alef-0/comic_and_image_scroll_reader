"""Command-line entry point and application event loop."""

import argparse
from collections.abc import Callable
from pathlib import Path

import FreeSimpleGUI as sg

from .bookshelf import scan_bookshelf
from .reader_view import ComicStrip
from .window import ask_for_bookshelf, build_reader_window, desktop_size, maximize


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
    return parser.parse_args(argv)


def _open_another_folder(reader: ComicStrip) -> None:
    selected = ask_for_bookshelf(reader.folder, reader.window.TKroot)
    if selected is None:
        return
    pages = scan_bookshelf(selected) if selected.is_dir() else []
    if pages:
        reader.open_bookshelf(pages, selected)
    else:
        sg.popup_error(f"No readable supported images were found in:\n{selected}")


def run_reader(folder: Path) -> int:
    if not folder.is_dir():
        sg.popup_error(f"This is not a folder:\n{folder}")
        return 2
    pages = scan_bookshelf(folder)
    if not pages:
        sg.popup_error(f"No readable supported images were found in:\n{folder}")
        return 1

    screen_width, _screen_height = desktop_size()
    window = build_reader_window()
    try:
        maximize(window)
        window.refresh()
        reader = ComicStrip(window, pages, folder, screen_width)
        actions: dict[str, Callable[[], None]] = {
            "-OPEN-": lambda: _open_another_folder(reader),
            "-ZOOM-OUT-": lambda: reader.zoom(-1),
            "-ZOOM-IN-": lambda: reader.zoom(1),
            "-FIT-": reader.fit_width,
            "-FULLSCREEN-": reader.toggle_fullscreen,
        }
        while not reader.should_close:
            event, _values = window.read(timeout=50)
            if event == sg.WIN_CLOSED:
                break
            action = actions.get(event)
            if action is not None:
                action()
    finally:
        window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = read_arguments(argv)
    folder = arguments.folder.expanduser() if arguments.folder else ask_for_bookshelf()
    return 0 if folder is None else run_reader(folder)

