"""Command-line entry point and application event loop."""

import argparse
from collections.abc import Callable
from pathlib import Path
import tkinter as tk

import FreeSimpleGUI as sg

from .config import CONFIG_PATH, load_config, save_config
from .files.bookshelf import load_pages, scan_bookshelf
from .reading_progress import (
    ReadingProgress,
    progress_for_folder,
    save_reading_progress,
)
from .ui.launcher import dropped_target, run_launcher
from .ui.reader_view import ComicStrip
from .ui.window import (
    READER_DROP_EVENT_KEY,
    ask_for_page_number,
    ask_for_bookshelf,
    build_reader_window,
    desktop_size,
    enable_window_drop,
    is_maximized,
    maximize,
    PAGE_COUNTER_KEY,
    TOP_BAR_TOGGLE_KEY,
    TOP_BAR_KEY,
    ZOOM_IN_KEY,
    ZOOM_OUT_KEY,
    reader_window_title,
    size_from_geometry,
    toggle_collapsible_group,
    toggle_top_bar,
    windowed_size,
)


def read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display a folder of images as a vertically scrolling comic."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="folder or image files containing comic pages; omit it to use the folder picker",
    )
    parser.add_argument(
        "--windowed",
        "--no-maximize",
        "--not-maximized",
        action="store_false",
        dest="start_maximized",
        default=None,
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
    parser.add_argument(
        "--expand-all",
        action="store_true",
        help="start with every collapsible toolbar menu open for UI inspection",
    )
    args = parser.parse_args(argv)
    if not args.paths:
        args.folder = None
        args.images = []
    elif len(args.paths) == 1 and not args.paths[0].is_file():
        args.folder = args.paths[0]
        args.images = []
    else:
        args.folder = None
        args.images = args.paths
    return args


def _open_another_folder(reader: ComicStrip) -> None:
    reader.stop_zooming()
    start_folder = reader.folder if reader.folder.is_dir() else reader.folder.parent
    try:
        selected = ask_for_bookshelf(start_folder, reader.window.TKroot)
    except RuntimeError as error:
        sg.popup_error(f"Unable to open the system folder chooser:\n{error}")
        return
    if selected is None:
        return
    pages = scan_bookshelf(selected) if selected.is_dir() else []
    if pages:
        _save_current_progress(reader)
        reader.open_bookshelf(pages, selected, is_folder=True)
        _offer_to_resume(reader)
    else:
        sg.popup_error(f"No readable supported images were found in:\n{selected}")


def _save_current_progress(reader: ComicStrip) -> None:
    """Persist only the active folder and page when remembering is enabled."""
    if not reader.remember_folder or not getattr(reader, "is_folder", True):
        return
    try:
        save_reading_progress(
            ReadingProgress(
                folder=reader.folder,
                last_page=max(1, reader.current_page_number),
            )
        )
    except OSError as error:
        sg.popup_error(f"Unable to save reading progress:\n{error}")


def _offer_to_resume(reader: ComicStrip) -> None:
    """Offer to restore a saved position whenever a folder is reopened."""
    if not reader.remember_folder or not getattr(reader, "is_folder", True):
        return
    progress = progress_for_folder(reader.folder)
    if progress is None:
        return
    answer = sg.popup_yes_no(
        (
            f"Continue reading {reader.folder.name} from page "
            f"{progress.last_page}?"
        ),
        title="Continue reading?",
    )
    if answer == "Yes":
        reader.go_to_page_number(progress.last_page)


def _save_current_config(reader: ComicStrip) -> None:
    config = load_config()
    config.update(
        {
            "prevent_image_upscale": reader.prevent_image_upscale,
            "stop_at_fit_width": reader.stop_at_fit_width,
            "dual_page": reader.dual_page,
            "manga_reading": reader.manga_reading,
            "page_spacing": reader.page_spacing,
            "detect_double_spreads": reader.detect_double_spreads,
            "remember_folder": reader.remember_folder,
            "zoom_level": reader.reading_zoom_level,
            "top_bar_visible": bool(
                reader.window[TOP_BAR_KEY].metadata["visible"]
            ),
        }
    )
    try:
        save_config(config)
    except OSError as error:
        sg.popup_error(f"Unable to save configurations:\n{error}")
        return
    sg.popup_ok(f"Configurations saved to:\n{CONFIG_PATH}", title="Save Configs")


def _save_top_bar_visibility(reader: ComicStrip) -> None:
    """Persist only the top-bar state without saving other unsaved controls."""
    config = load_config()
    config["top_bar_visible"] = bool(
        reader.window[TOP_BAR_KEY].metadata["visible"]
    )
    try:
        save_config(config)
    except OSError as error:
        sg.popup_error(f"Unable to save the top-bar state:\n{error}")


def _save_remember_folder(reader: ComicStrip) -> None:
    """Persist the experimental folder-memory toggle immediately."""
    config = load_config()
    config["remember_folder"] = reader.remember_folder
    try:
        save_config(config)
    except OSError as error:
        sg.popup_error(f"Unable to save the folder-memory option:\n{error}")


def _save_global_zoom(reader: ComicStrip) -> None:
    """Persist one zoom level shared by every folder and reader window."""
    config = load_config()
    config["zoom_level"] = reader.reading_zoom_level
    try:
        save_config(config)
    except OSError as error:
        sg.popup_error(f"Unable to save the zoom level:\n{error}")


def _save_window_state(normal_geometry: str, maximized: bool) -> None:
    """Persist the last normal geometry and desktop maximized state."""
    config = load_config()
    config["window_geometry"] = normal_geometry
    config["window_maximized"] = maximized
    try:
        save_config(config)
    except OSError as error:
        sg.popup_error(f"Unable to save the window state:\n{error}")


def run_reader(
    target: Path | list[Path] | None = None,
    *,
    folder: Path | list[Path] | None = None,
    start_maximized: bool | None = None,
    dual_page: bool = False,
    manga_reading: bool = False,
    expand_all: bool = False,
) -> int:
    active_target = target if target is not None else folder
    if active_target is None:
        raise ValueError("A folder or image list must be provided.")

    is_folder = True
    if isinstance(active_target, list):
        pages = load_pages(active_target)
        if not pages:
            sg.popup_error("No readable supported images were found.")
            return 1
        active_folder = pages[0].file.parent
        is_folder = False
    elif active_target.is_file():
        pages = load_pages([active_target])
        if not pages:
            sg.popup_error(f"No readable supported images were found in:\n{active_target}")
            return 1
        active_folder = active_target.parent
        is_folder = False
    else:
        if not active_target.is_dir():
            sg.popup_error(f"This is not a folder:\n{active_target}")
            return 2
        pages = scan_bookshelf(active_target)
        if not pages:
            sg.popup_error(f"No readable supported images were found in:\n{active_target}")
            return 1
        active_folder = active_target
        is_folder = True

    config = load_config()
    dual_page = dual_page or config["dual_page"]
    manga_reading = manga_reading or config["manga_reading"]
    screen_width, screen_height = desktop_size()
    saved_geometry = config["window_geometry"]
    initial_size = size_from_geometry(saved_geometry) or windowed_size(
        screen_width, screen_height
    )
    title = (
        reader_window_title(active_folder)
        if is_folder
        else reader_window_title(active_folder, len(pages))
    )
    window = build_reader_window(
        initial_size,
        dual_page=dual_page,
        manga_reading=manga_reading,
        page_spacing=config["page_spacing"],
        detect_double_spreads=config["detect_double_spreads"],
        remember_folder=config["remember_folder"],
        prevent_image_upscale=config["prevent_image_upscale"],
        stop_at_fit_width=config["stop_at_fit_width"],
        top_bar_visible=config["top_bar_visible"],
        expand_all=expand_all,
        title=title,
    )
    enable_window_drop(window)
    reader: ComicStrip | None = None
    normal_geometry = str(saved_geometry) if size_from_geometry(saved_geometry) else ""
    maximized_on_close = False
    try:
        # Establish and paint a useful normal-window geometry first. Besides
        # making page one visible immediately, this gives the window manager a
        # real geometry to restore when the user leaves the maximized state.
        window.refresh()
        if normal_geometry:
            window.TKroot.geometry(normal_geometry)
            window.refresh()
        else:
            normal_geometry = window.TKroot.geometry()
        reader = ComicStrip(
            window,
            pages,
            active_folder,
            screen_width,
            dual_page=dual_page,
            manga_reading=manga_reading,
            page_spacing=config["page_spacing"],
            detect_double_spreads=config["detect_double_spreads"],
            remember_folder=config["remember_folder"],
            prevent_image_upscale=config["prevent_image_upscale"],
            stop_at_fit_width=config["stop_at_fit_width"],
            is_folder=is_folder,
        )
        reader.restore_zoom_level(str(config["zoom_level"]))
        if is_folder:
            _offer_to_resume(reader)
        window.refresh()
        should_maximize = (
            bool(config["window_maximized"])
            if start_maximized is None
            else start_maximized
        )
        if should_maximize:
            maximize(window)
            window.refresh()
        actions: dict[str, Callable[[], None]] = {
            "-OPEN-": lambda: _open_another_folder(reader),
            ZOOM_OUT_KEY: lambda: reader.zoom(-1),
            ZOOM_IN_KEY: lambda: reader.zoom(1),
            "-FIT-": reader.fit_width,
            "-ORIGINAL-SIZE-": reader.show_original_size,
            "-SAVE-CONFIGS-": lambda: _save_current_config(reader),
        }
        while not reader.should_close:
            event, values = window.read(timeout=50)
            if event == sg.WIN_CLOSED:
                break
            maximized_on_close = is_maximized(window)
            if not maximized_on_close:
                try:
                    normal_geometry = window.TKroot.geometry()
                except tk.TclError:
                    pass
            if toggle_collapsible_group(window, event):
                continue
            if event == READER_DROP_EVENT_KEY:
                dropped = dropped_target(
                    values.get(READER_DROP_EVENT_KEY), window.TKroot
                )
                if dropped is None:
                    sg.popup_error(
                        "Drop a folder or images containing comic pages."
                    )
                    continue
                if isinstance(dropped, list):
                    new_pages = load_pages(dropped)
                    if not new_pages:
                        sg.popup_error(
                            "No readable supported images were found."
                        )
                        continue
                    _save_current_progress(reader)
                    reader.open_bookshelf(
                        new_pages, new_pages[0].file.parent, is_folder=False
                    )
                elif dropped.is_dir():
                    new_pages = scan_bookshelf(dropped)
                    if not new_pages:
                        sg.popup_error(
                            f"No readable supported images were found in:\n{dropped}"
                        )
                        continue
                    _save_current_progress(reader)
                    reader.open_bookshelf(new_pages, dropped, is_folder=True)
                    _offer_to_resume(reader)
                continue
            if event == TOP_BAR_TOGGLE_KEY:
                toggle_top_bar(window)
                _save_top_bar_visibility(reader)
                continue
            if event == PAGE_COUNTER_KEY:
                page_number = ask_for_page_number(
                    reader.current_page_number, len(reader.pages)
                )
                if page_number is not None:
                    reader.go_to_page_number(page_number)
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
            if event == "-PAGE-SPACING-":
                reader.set_page_spacing(bool(values["-PAGE-SPACING-"]))
                continue
            if event == "-DETECT-DOUBLE-SPREADS-":
                reader.set_double_spread_detection(
                    bool(values["-DETECT-DOUBLE-SPREADS-"])
                )
                continue
            if event == "-REMEMBER-FOLDER-":
                reader.remember_folder = bool(values["-REMEMBER-FOLDER-"])
                _save_remember_folder(reader)
                continue
            action = actions.get(event)
            if action is not None:
                action()
    finally:
        if reader is not None:
            _save_global_zoom(reader)
            _save_current_progress(reader)
        _save_window_state(normal_geometry, maximized_on_close)
        window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = read_arguments(argv)
    target: Path | list[Path] | None = None
    if getattr(arguments, "images", None):
        target = [path.expanduser() for path in arguments.images]
    elif arguments.folder:
        target = arguments.folder.expanduser()
    else:
        target = run_launcher()

    while target is not None:
        result = run_reader(
            target,
            start_maximized=arguments.start_maximized,
            dual_page=arguments.dual_page,
            manga_reading=arguments.manga_reading,
            expand_all=arguments.expand_all,
        )
        if result == 0:
            return 0
        target = run_launcher()
    return 0

