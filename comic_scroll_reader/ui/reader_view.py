"""Virtualized, scrollable comic canvas."""

from collections import deque
from collections.abc import Callable
from pathlib import Path
from statistics import median
import tkinter as tk
from tkinter import ttk

import FreeSimpleGUI as sg
from PIL import Image, ImageTk

from ..core.layout import (
    arrange_pages,
    clamp_scroll,
    detect_double_spread_indices,
    neighboring_page_indices,
    pages_nearest_to,
    visible_page_range,
)
from ..core.memory import MemoryShelf
from ..core.models import ComicPage, PagePosition
from ..files.bookshelf import open_page
from ..imaging.image_resizer import ImageResizer
from .window import CANVAS_COLOR, PAGE_COUNTER_KEY, reader_window_title


PhotoKey = tuple[Path, int, int]
CanvasPage = tuple[int, ImageTk.PhotoImage, tuple[int, int]]


def _decoded_bytes(_file: Path, image: Image.Image) -> int:
    return image.width * image.height * len(image.getbands())


def _photo_bytes(key: PhotoKey, _photo: ImageTk.PhotoImage) -> int:
    return key[1] * key[2] * 4


def _resize_filter(
    _source_size: tuple[int, int], _target_size: tuple[int, int]
) -> Image.Resampling:
    """Use bicubic interpolation for every resize operation."""
    return Image.Resampling.BICUBIC


class ComicStrip:
    """Manage page geometry, rendering, input, and image caches."""

    START_WIDTH_RATIO = 0.75
    MIN_WIDTH_RATIO = 0.10
    MAX_WIDTH_RATIO = 4.0
    ZOOM_FACTOR = 1.10
    ZOOM_STEP_DELAY_MS = 1
    WHEEL_STEP = 120
    SOURCE_CACHE_BYTES = 256 * 1024 * 1024
    PHOTO_CACHE_BYTES = 192 * 1024 * 1024
    IMMEDIATE_RENDER_COUNT = 2
    RENDER_STEP_DELAY_MS = 1
    PRELOAD_DELAY_MS = 150
    PRELOAD_STEP_DELAY_MS = 10
    PRELOAD_DISTANCE = 2
    CONTROL_MASK = 0x0004
    PAGE_GAP_SIZE = 12

    def __init__(
        self,
        window: sg.Window,
        pages: list[ComicPage],
        folder: Path,
        desktop_width: int,
        *,
        dual_page: bool = False,
        manga_reading: bool = False,
        page_spacing: bool = True,
        detect_double_spreads: bool = True,
        prevent_image_upscale: bool = False,
        stop_at_fit_width: bool = True,
    ) -> None:
        self.window = window
        self.canvas: tk.Canvas = window["-CANVAS-"].TKCanvas
        self.pages = pages
        self.folder = folder
        self.desktop_width = desktop_width
        self.viewport_width = max(1, self.canvas.winfo_width())
        self.viewport_height = max(1, self.canvas.winfo_height())
        self.prevent_image_upscale = prevent_image_upscale
        self.stop_at_fit_width = stop_at_fit_width
        self.dual_page = dual_page
        self.manga_reading = manga_reading
        self.page_spacing = page_spacing
        self.detect_double_spreads = detect_double_spreads
        self.double_spread_indices: set[int] = set()
        self.typical_page_ratio = 1.0
        self._refresh_spread_analysis()
        self.original_size = False
        initial_width = max(1, round(desktop_width * self.START_WIDTH_RATIO))
        self.strip_width = min(initial_width, self._maximum_strip_width())
        self.scroll_y = 0
        self.pan_x = 0
        self.positions: list[PagePosition] = []
        self.source_images = MemoryShelf(self.SOURCE_CACHE_BYTES, _decoded_bytes)
        self.ready_photos = MemoryShelf(self.PHOTO_CACHE_BYTES, _photo_bytes)
        self.image_resizer = ImageResizer()
        self.canvas_pages: dict[Path, CanvasPage] = {}
        self.should_close = False
        self.fullscreen = False
        self._windowed_geometry = self.window.TKroot.geometry()
        self._windowed_maximized = self._is_maximized()
        self._resize_job: str | None = None
        self._zoom_job: str | None = None
        self._render_job: str | None = None
        self._preload_job: str | None = None
        self._zoom_stash: deque[tuple[int, int]] = deque()
        self._pending_render_indices: list[int] = []
        self._pending_preload_indices: list[int] = []
        self._visible_range = (0, 0)
        self._drag_last: tuple[int, int] | None = None
        self.canvas.configure(
            background=CANVAS_COLOR,
            borderwidth=0,
            highlightthickness=0,
            cursor="fleur",
        )
        # Overlaying the scrollbar keeps the comic's drawing width unchanged.
        self.scrollbar = ttk.Scrollbar(
            self.canvas,
            orient=tk.VERTICAL,
            command=self._scrollbar_moved,
            cursor="arrow",
        )
        self.scrollbar.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self._connect_controls()
        self._arrange_strip()
        self.paint()
        self._show_status()

    @property
    def content_height(self) -> int:
        return max((position.bottom for position in self.positions), default=0)

    @property
    def maximum_scroll(self) -> int:
        return max(0, self.content_height - self.viewport_height)

    @property
    def current_page_number(self) -> int:
        """Return the last visible page as a user-facing one-based number."""
        return self._visible_range[1] if self.pages else 0

    @property
    def page_counter_text(self) -> str:
        """Describe the last visible page or its active dual-page spread."""
        total = len(self.pages)
        if not total:
            return "0 / 0"
        last_index = self.current_page_number - 1
        if not self.dual_page:
            return f"{last_index + 1} / {total}"

        row_y = self.positions[last_index].y
        first_index = last_index
        while first_index > 0 and self.positions[first_index - 1].y == row_y:
            first_index -= 1
        if first_index == last_index:
            return f"{last_index + 1} / {total}"
        return f"{first_index + 1}-{last_index + 1} / {total}"

    def _connect_controls(self) -> None:
        self.canvas.bind("<MouseWheel>", self._wheel_moved)
        self.canvas.bind("<Button-4>", self._wheel_moved)
        self.canvas.bind("<Button-5>", self._wheel_moved)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag_moved)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.scrollbar.bind("<MouseWheel>", self._wheel_moved)
        self.scrollbar.bind("<Button-4>", self._wheel_moved)
        self.scrollbar.bind("<Button-5>", self._wheel_moved)

        for sequence, action in self._shortcut_actions().items():
            self.window.TKroot.bind(sequence, self._keyboard_action(action))
        self.canvas.focus_set()

    def _shortcut_actions(self) -> dict[str, Callable[[], None]]:
        """Return keyboard bindings as actions that can be verified independently."""
        one_screen = lambda: max(1, self.viewport_height - 50)
        page_down = lambda: self.scroll(one_screen())
        page_up = lambda: self.scroll(-one_screen())
        go_home = lambda: self.scroll(-self.maximum_scroll)
        go_end = lambda: self.scroll(self.maximum_scroll)
        pan_left = lambda: self.pan_horizontally(self.WHEEL_STEP)
        pan_right = lambda: self.pan_horizontally(-self.WHEEL_STEP)
        return {
            "<Down>": lambda: self.scroll(self.WHEEL_STEP),
            "<KP_Down>": lambda: self.scroll(self.WHEEL_STEP),
            "<j>": lambda: self.scroll(self.WHEEL_STEP),
            "<s>": lambda: self.scroll(self.WHEEL_STEP),
            "<Up>": lambda: self.scroll(-self.WHEEL_STEP),
            "<KP_Up>": lambda: self.scroll(-self.WHEEL_STEP),
            "<k>": lambda: self.scroll(-self.WHEEL_STEP),
            "<w>": lambda: self.scroll(-self.WHEEL_STEP),
            "<Left>": pan_left,
            "<KP_Left>": pan_left,
            "<Right>": pan_right,
            "<KP_Right>": pan_right,
            "<Next>": page_down,
            "<KP_Next>": page_down,
            "<space>": page_down,
            "<Prior>": page_up,
            "<KP_Prior>": page_up,
            "<Home>": go_home,
            "<KP_Home>": go_home,
            "<g>": go_home,
            "<End>": go_end,
            "<KP_End>": go_end,
            "<G>": go_end,
            "<Control-plus>": lambda: self.zoom(1),
            "<Control-equal>": lambda: self.zoom(1),
            "<Control-minus>": lambda: self.zoom(-1),
            "<F11>": self.toggle_fullscreen,
            "<Escape>": self.request_close,
            "<q>": self.request_close,
            "<Q>": self.request_close,
        }

    @staticmethod
    def _keyboard_action(action: Callable[[], None]) -> Callable[[tk.Event], str]:
        def handle(_event: tk.Event) -> str:
            action()
            return "break"

        return handle

    def request_close(self) -> None:
        self.stop_zooming()
        self.should_close = True

    def _is_maximized(self) -> bool:
        try:
            return bool(self.window.TKroot.attributes("-zoomed"))
        except tk.TclError:
            return self.window.TKroot.state() == "zoomed"

    def toggle_fullscreen(self) -> None:
        self.stop_zooming()
        root: tk.Tk = self.window.TKroot
        if not self.fullscreen:
            root.update_idletasks()
            self._windowed_geometry = root.geometry()
            self._windowed_maximized = self._is_maximized()
            root.attributes("-fullscreen", True)
            self.fullscreen = True
            return

        root.attributes("-fullscreen", False)
        self.fullscreen = False
        root.after_idle(self._restore_window)

    def _restore_window(self) -> None:
        root: tk.Tk = self.window.TKroot
        try:
            root.attributes("-zoomed", self._windowed_maximized)
        except tk.TclError:
            root.state("zoomed" if self._windowed_maximized else "normal")
        if not self._windowed_maximized:
            root.geometry(self._windowed_geometry)

    def _canvas_resized(self, event: tk.Event) -> None:
        size = (max(1, int(event.width)), max(1, int(event.height)))
        if size == (self.viewport_width, self.viewport_height):
            return
        self.stop_zooming()
        self.viewport_width, self.viewport_height = size
        if self._resize_job is not None:
            self.canvas.after_cancel(self._resize_job)
        self._resize_job = self.canvas.after(40, self._finish_resize)

    def _finish_resize(self) -> None:
        self._resize_job = None
        if self.original_size:
            self._arrange_strip()
            self.paint()
            self._show_status()
            return
        maximum = self._maximum_strip_width()
        if self.strip_width > maximum:
            self._set_strip_width(maximum, self.viewport_height // 2)
            return
        self._arrange_strip()
        self.paint()
        self._show_status()

    def _wheel_moved(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            direction, steps = 1, 1
        elif getattr(event, "num", None) == 5:
            direction, steps = -1, 1
        else:
            delta = int(getattr(event, "delta", 0))
            if delta == 0:
                return "break"
            direction = 1 if delta > 0 else -1
            steps = max(1, abs(delta) // 120)

        if int(getattr(event, "state", 0)) & self.CONTROL_MASK:
            # Keep zoom visually sequential even when a mouse reports several
            # wheel notches in one event. Scrolling can still use acceleration.
            self.zoom(direction, anchor_y=int(event.y))
        else:
            self.scroll(-direction * steps * self.WHEEL_STEP)
        return "break"

    def _start_drag(self, event: tk.Event) -> str:
        self.canvas.focus_set()
        self.stop_zooming()
        self._drag_last = (int(event.x), int(event.y))
        return "break"

    def _drag_moved(self, event: tk.Event) -> str:
        current = (int(event.x), int(event.y))
        if self._drag_last is None:
            self._drag_last = current
            return "break"
        delta_x = current[0] - self._drag_last[0]
        delta_y = current[1] - self._drag_last[1]
        self._drag_last = current
        new_pan_x = self._clamped_pan_x(self.pan_x + delta_x)
        new_scroll_y = clamp_scroll(
            self.scroll_y - delta_y,
            self.content_height,
            self.viewport_height,
        )
        if (new_pan_x, new_scroll_y) != (self.pan_x, self.scroll_y):
            self.pan_x = new_pan_x
            self.scroll_y = new_scroll_y
            self.paint()
        return "break"

    def _end_drag(self, _event: tk.Event) -> str:
        self._drag_last = None
        return "break"

    def _refresh_spread_analysis(self) -> None:
        if self.detect_double_spreads:
            self.double_spread_indices = detect_double_spread_indices(self.pages)
        else:
            self.double_spread_indices = set()
        regular_ratios = [
            page.native_width / page.native_height
            for index, page in enumerate(self.pages)
            if index not in self.double_spread_indices
        ]
        all_ratios = [page.native_width / page.native_height for page in self.pages]
        self.typical_page_ratio = median(regular_ratios or all_ratios or [1.0])

    def _scaled_page_widths(self) -> list[int]:
        widths = [
            min(self.strip_width, page.native_width)
            if self.prevent_image_upscale
            else self.strip_width
            for page in self.pages
        ]
        target_height = self.strip_width / max(self.typical_page_ratio, 0.01)
        for index in self.double_spread_indices:
            page = self.pages[index]
            page_ratio = page.native_width / page.native_height
            width = max(1, round(target_height * page_ratio))
            width = min(
                width, max(1, round(self.desktop_width * self.MAX_WIDTH_RATIO))
            )
            if self.stop_at_fit_width:
                width = min(width, self._fitted_page_width(1))
            if self.prevent_image_upscale:
                width = min(width, page.native_width)
            widths[index] = width
        return widths

    def _arrange_strip(self) -> None:
        page_widths = self._scaled_page_widths()
        if self.original_size:
            page_widths = [
                self._original_page_width(index) for index in range(len(self.pages))
            ]
        self.positions = arrange_pages(
            self.pages,
            page_widths,
            self.viewport_width,
            dual_page=self.dual_page,
            manga_reading=self.manga_reading,
            page_gap=self.PAGE_GAP_SIZE if self.page_spacing else 0,
            solo_page_indices=self.double_spread_indices,
        )
        self.scroll_y = clamp_scroll(
            self.scroll_y, self.content_height, self.viewport_height
        )
        self.pan_x = self._clamped_pan_x(self.pan_x)

    def _clamped_pan_x(self, pan_x: int) -> int:
        if not self.positions:
            return 0
        content_left = min(position.x for position in self.positions)
        content_right = max(
            position.x + position.width for position in self.positions
        )
        if content_right - content_left <= self.viewport_width:
            return 0
        minimum = self.viewport_width - content_right
        maximum = -content_left
        return min(max(pan_x, minimum), maximum)

    def _source_image(self, page: ComicPage) -> Image.Image | None:
        image = self.source_images.get(page.file)
        if image is None:
            image = open_page(page.file)
            if image is not None:
                self.source_images.store(page.file, image)
        return image

    def _canvas_photo(
        self, page: ComicPage, width: int, height: int
    ) -> ImageTk.PhotoImage | None:
        cache_key = (page.file, width, height)
        cached = self.ready_photos.get(cache_key)
        if cached is not None:
            return cached

        original = self._source_image(page)
        if original is None:
            return None
        rendered = original
        if original.size != (width, height):
            rendered = self.image_resizer.resize(
                original,
                (width, height),
                _resize_filter(original.size, (width, height)),
            )

        photo = ImageTk.PhotoImage(rendered, master=self.canvas)
        self.ready_photos.store(cache_key, photo)
        return photo

    def scroll(self, pixels: int) -> None:
        self.scroll_to(self.scroll_y + pixels)

    def pan_horizontally(self, pixels: int) -> None:
        """Move across a page that is wider than the viewport."""
        self.stop_zooming()
        target = self._clamped_pan_x(self.pan_x + pixels)
        if target != self.pan_x:
            self.pan_x = target
            self.paint()

    def scroll_to(self, position: int | float) -> None:
        """Move directly to a position selected on the scrollbar."""
        self.stop_zooming()
        target = clamp_scroll(round(position), self.content_height, self.viewport_height)
        if target != self.scroll_y:
            self.scroll_y = target
            self.paint()

    def go_to_page(self, index: int) -> None:
        """Move to a zero-based page index, clamped to the available pages."""
        if not self.positions:
            return
        page_index = min(max(index, 0), len(self.positions) - 1)
        self.scroll_to(self.positions[page_index].y)

    def go_to_page_number(self, value: object) -> bool:
        """Move to a user-facing one-based page number when it is an integer."""
        try:
            page_number = int(str(value).strip())
        except (TypeError, ValueError):
            return False
        self.go_to_page(page_number - 1)
        return True

    def _scrollbar_moved(
        self, action: str, amount: str, unit: str | None = None
    ) -> None:
        if action == "moveto":
            self.scroll_to(float(amount) * self.content_height)
            return
        if action == "scroll":
            distance = (
                max(1, self.viewport_height - 50)
                if unit == "pages"
                else self.WHEEL_STEP
            )
            self.scroll(int(amount) * distance)

    def zoom(self, steps: int, anchor_y: int | None = None) -> None:
        if steps == 0:
            return
        anchor = self.viewport_height // 2 if anchor_y is None else anchor_y
        anchor = min(max(anchor, 0), self.viewport_height)
        direction = 1 if steps > 0 else -1

        # A new direction supersedes queued work. Repeated commands in the same
        # direction join the stash and are applied one at a time.
        if self._zoom_stash and self._zoom_stash[-1][0] != direction:
            self.stop_zooming()
        else:
            self._cancel_pending_work()
        self._zoom_stash.extend((direction, anchor) for _ in range(abs(steps)))
        if self._zoom_job is None:
            self._zoom_job = self.canvas.after(
                0, self._apply_next_zoom_step
            )

    def _apply_next_zoom_step(self) -> None:
        self._zoom_job = None
        if not self._zoom_stash:
            return
        direction, anchor = self._zoom_stash.popleft()
        if not self._apply_zoom_step(direction, anchor):
            self._zoom_stash.clear()
            return
        if self._zoom_stash:
            self._zoom_job = self.canvas.after(
                self.ZOOM_STEP_DELAY_MS, self._apply_next_zoom_step
            )

    def _apply_zoom_step(self, direction: int, anchor: int) -> bool:
        maximum = self._maximum_strip_width()
        minimum = min(
            maximum,
            max(1, round(self.desktop_width * self.MIN_WIDTH_RATIO)),
        )
        requested = round(self.strip_width * (self.ZOOM_FACTOR**direction))
        new_width = min(max(requested, minimum), maximum)
        return self._set_strip_width(new_width, anchor)

    def _set_strip_width(self, new_width: int, anchor: int) -> bool:
        was_original_size = self.original_size
        if new_width == self.strip_width and not was_original_size:
            return False
        old_height = max(1, self.content_height)
        reading_position = (self.scroll_y + anchor) / old_height
        self.original_size = False
        self.strip_width = new_width
        self._arrange_strip()
        anchored_scroll = round(reading_position * self.content_height - anchor)
        self.scroll_y = clamp_scroll(
            anchored_scroll, self.content_height, self.viewport_height
        )
        self.paint(priority_y=anchor)
        self._show_status()
        return True

    def fit_width(self) -> None:
        self.stop_zooming()
        target_width = self._fitted_page_width(2 if self.dual_page else 1)
        if self.prevent_image_upscale:
            target_width = min(target_width, self._widest_native_width())
        self._set_strip_width(target_width, self.viewport_height // 2)

    def show_original_size(self) -> None:
        """Display every page at native size, reducing only to obey fit limits."""
        self.stop_zooming()
        old_height = max(1, self.content_height)
        anchor = self.viewport_height // 2
        reading_position = (self.scroll_y + anchor) / old_height
        self.original_size = True
        self._arrange_strip()
        self.scroll_y = clamp_scroll(
            round(reading_position * self.content_height - anchor),
            self.content_height,
            self.viewport_height,
        )
        self.paint(priority_y=anchor)
        self._show_status()

    def _original_page_width(self, index: int) -> int:
        native_width = self.pages[index].native_width
        if not self.stop_at_fit_width:
            return native_width
        is_solo = index == 0 or index in self.double_spread_indices
        columns = 2 if self.dual_page and not is_solo else 1
        return min(native_width, self._fitted_page_width(columns))

    def _fitted_page_width(self, columns: int) -> int:
        gap = (
            self.PAGE_GAP_SIZE
            if columns > 1 and getattr(self, "page_spacing", True)
            else 0
        )
        return max(1, (self.viewport_width - gap) // columns)

    def _widest_native_width(self) -> int:
        return max((page.native_width for page in self.pages), default=1)

    def _maximum_strip_width(self) -> int:
        maximum = max(1, round(self.desktop_width * self.MAX_WIDTH_RATIO))
        if self.stop_at_fit_width:
            columns = 2 if self.dual_page else 1
            maximum = min(maximum, self._fitted_page_width(columns))
        if self.prevent_image_upscale:
            maximum = min(maximum, self._widest_native_width())
        return max(1, maximum)

    def set_zoom_limits(
        self, *, prevent_image_upscale: bool, stop_at_fit_width: bool
    ) -> None:
        self.stop_zooming()
        self.prevent_image_upscale = prevent_image_upscale
        self.stop_at_fit_width = stop_at_fit_width
        if self.original_size:
            self._arrange_strip()
            self.paint()
            self._show_status()
            return
        maximum = self._maximum_strip_width()
        if self.strip_width > maximum:
            self._set_strip_width(maximum, self.viewport_height // 2)
            return
        self._arrange_strip()
        self.paint(priority_y=self.viewport_height // 2)
        self._show_status()

    def set_page_layout(self, *, dual_page: bool, manga_reading: bool) -> None:
        self.stop_zooming()
        old_height = max(1, self.content_height)
        anchor = self.viewport_height // 2
        reading_position = (self.scroll_y + anchor) / old_height
        self.dual_page = dual_page
        self.manga_reading = manga_reading
        if not self.original_size:
            self.strip_width = min(self.strip_width, self._maximum_strip_width())
        self._arrange_strip()
        self.scroll_y = clamp_scroll(
            round(reading_position * self.content_height - anchor),
            self.content_height,
            self.viewport_height,
        )
        self.paint(priority_y=anchor)
        self._show_status()

    def set_page_spacing(self, enabled: bool) -> None:
        """Show or hide background-colored gaps between neighboring pages."""
        if enabled == self.page_spacing:
            return
        self.stop_zooming()
        old_height = max(1, self.content_height)
        anchor = self.viewport_height // 2
        reading_position = (self.scroll_y + anchor) / old_height
        self.page_spacing = enabled
        if not self.original_size:
            self.strip_width = min(self.strip_width, self._maximum_strip_width())
        self._arrange_strip()
        self.scroll_y = clamp_scroll(
            round(reading_position * self.content_height - anchor),
            self.content_height,
            self.viewport_height,
        )
        self.paint(priority_y=anchor)
        self._show_status()

    def set_double_spread_detection(self, enabled: bool) -> None:
        """Toggle ratio-based spread detection while preserving reading position."""
        if enabled == self.detect_double_spreads:
            return
        self.stop_zooming()
        old_height = max(1, self.content_height)
        anchor = self.viewport_height // 2
        reading_position = (self.scroll_y + anchor) / old_height
        self.detect_double_spreads = enabled
        self._refresh_spread_analysis()
        self._arrange_strip()
        self.scroll_y = clamp_scroll(
            round(reading_position * self.content_height - anchor),
            self.content_height,
            self.viewport_height,
        )
        self.paint(priority_y=anchor)
        self._show_status()

    def open_bookshelf(self, pages: list[ComicPage], folder: Path) -> None:
        self.stop_zooming()
        self.pages = pages
        self.folder = folder
        self.window.set_title(reader_window_title(folder))
        self._refresh_spread_analysis()
        self.original_size = False
        initial_width = max(1, round(self.viewport_width * self.START_WIDTH_RATIO))
        self.strip_width = min(initial_width, self._maximum_strip_width())
        self.scroll_y = 0
        self.pan_x = 0
        self.source_images.clear()
        self.ready_photos.clear()
        self._clear_canvas()
        self._arrange_strip()
        self.paint()
        self._show_status()

    def stop_zooming(self) -> None:
        """Discard queued zoom steps and rendering left by the last step."""
        if self._zoom_job is not None:
            self.canvas.after_cancel(self._zoom_job)
            self._zoom_job = None
        self._zoom_stash.clear()
        self._cancel_pending_work()

    def _clear_canvas(self) -> None:
        self._cancel_pending_work()
        for item, _photo, _size in self.canvas_pages.values():
            self.canvas.delete(item)
        self.canvas_pages.clear()

    def _cancel_pending_work(self) -> None:
        if self._render_job is not None:
            self.canvas.after_cancel(self._render_job)
            self._render_job = None
        if self._preload_job is not None:
            self.canvas.after_cancel(self._preload_job)
            self._preload_job = None
        self._pending_render_indices.clear()
        self._pending_preload_indices.clear()

    def _render_page(self, index: int) -> None:
        page = self.pages[index]
        position = self.positions[index]
        size = (position.width, position.height)
        existing = self.canvas_pages.get(page.file)
        if existing is not None and existing[2] == size:
            self.canvas.coords(
                existing[0], position.x + self.pan_x, position.y - self.scroll_y
            )
            return

        # Do the expensive resize before removing the old canvas image. During
        # rapid zooming, that old image is a better placeholder than a blank gap.
        photo = self._canvas_photo(page, *size)
        if photo is None:
            return
        if existing is not None:
            self.canvas.delete(existing[0])
        item = self.canvas.create_image(
            position.x + self.pan_x,
            position.y - self.scroll_y,
            anchor="nw",
            image=photo,
            tags=("comic-page",),
        )
        self.canvas_pages[page.file] = (item, photo, size)

    def paint(self, priority_y: int | None = None) -> None:
        self._cancel_pending_work()

        first, last = visible_page_range(
            self.positions, self.scroll_y, self.viewport_height
        )
        wanted_files = {self.pages[index].file for index in range(first, last)}
        for index in range(first, last):
            page = self.pages[index]
            position = self.positions[index]
            size = (position.width, position.height)
            existing = self.canvas_pages.get(page.file)
            if existing is not None and existing[2] == size:
                self.canvas.coords(
                    existing[0],
                    position.x + self.pan_x,
                    position.y - self.scroll_y,
                )
            elif existing is not None:
                placeholder_x = (
                    position.x
                    + (position.width - existing[2][0]) // 2
                    + self.pan_x
                )
                self.canvas.coords(
                    existing[0], placeholder_x, position.y - self.scroll_y
                )

        for file in set(self.canvas_pages) - wanted_files:
            item, _photo, _size = self.canvas_pages.pop(file)
            self.canvas.delete(item)

        viewport_anchor = self.viewport_height // 2 if priority_y is None else priority_y
        content_anchor = self.scroll_y + min(max(viewport_anchor, 0), self.viewport_height)
        needs_render = [
            index
            for index in range(first, last)
            if (
                self.pages[index].file not in self.canvas_pages
                or self.canvas_pages[self.pages[index].file][2]
                != (self.positions[index].width, self.positions[index].height)
            )
        ]
        prioritized = pages_nearest_to(self.positions, needs_render, content_anchor)
        for index in prioritized[: self.IMMEDIATE_RENDER_COUNT]:
            self._render_page(index)

        self.canvas.tag_lower("comic-page")
        self._visible_range = (first, last)
        self._pending_render_indices = prioritized[self.IMMEDIATE_RENDER_COUNT :]
        if self._pending_render_indices:
            self._render_job = self.canvas.after(
                self.RENDER_STEP_DELAY_MS, self._render_next_page
            )
        else:
            self._schedule_neighbor_preload()
        self._sync_scrollbar()
        self._show_page_counter()
        # Tk likes to batch wheel-driven paints. Flushing idle work makes the
        # reader feel immediate without forcing a full event-loop update.
        self.canvas.update_idletasks()

    def _render_next_page(self) -> None:
        self._render_job = None
        if not self._pending_render_indices:
            self._schedule_neighbor_preload()
            return

        index = self._pending_render_indices.pop(0)
        first, last = self._visible_range
        if first <= index < last:
            self._render_page(index)
            self.canvas.update_idletasks()

        if self._pending_render_indices:
            self._render_job = self.canvas.after(
                self.RENDER_STEP_DELAY_MS, self._render_next_page
            )
        else:
            self._schedule_neighbor_preload()

    def _sync_scrollbar(self) -> None:
        if self.content_height <= self.viewport_height:
            self.scrollbar.set(0.0, 1.0)
            self.scrollbar.state(["disabled"])
            return
        self.scrollbar.state(["!disabled"])
        self.scrollbar.set(
            self.scroll_y / self.content_height,
            (self.scroll_y + self.viewport_height) / self.content_height,
        )

    def _schedule_neighbor_preload(self) -> None:
        first, last = self._visible_range
        nearby = neighboring_page_indices(
            first, last, len(self.pages), self.PRELOAD_DISTANCE
        )
        self._pending_preload_indices = nearby
        if nearby:
            self._preload_job = self.canvas.after(
                self.PRELOAD_DELAY_MS, self._warm_next_neighbor
            )

    def _warm_next_neighbor(self) -> None:
        self._preload_job = None
        if not self._pending_preload_indices:
            return
        index = self._pending_preload_indices.pop(0)
        page = self.pages[index]
        position = self.positions[index]
        self._canvas_photo(page, position.width, position.height)
        if self._pending_preload_indices:
            self._preload_job = self.canvas.after(
                self.PRELOAD_STEP_DELAY_MS, self._warm_next_neighbor
            )

    def _show_status(self) -> None:
        if self.original_size:
            zoom = "Original"
        else:
            zoom = f"{round(100 * self.strip_width / max(1, self.viewport_width))}%"
        self.window["-STATUS-"].update(f"{self.folder.name}  •  {zoom}")

    def _show_page_counter(self) -> None:
        self.window[PAGE_COUNTER_KEY].update(value=self.page_counter_text)
