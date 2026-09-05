"""Virtualized, scrollable comic canvas."""

from collections import deque
from collections.abc import Callable
import io
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
    visible_page_range,
)
from ..core.memory import MemoryShelf, trim_memory
from ..core.models import ComicPage, PagePosition
from ..files.bookshelf import PDF_SUFFIXES, get_page_thumbnail, render_page_region
from ..imaging.async_renderer import AsyncRenderer
from .window import (
    CANVAS_COLOR,
    PAGE_COUNTER_KEY,
    ZOOM_STATUS_KEY,
    reader_window_title,
)


RegionKey = tuple[Path, int, int, int, int, int, int]
CanvasTile = tuple[int, ImageTk.PhotoImage, RegionKey]


def _photo_bytes(key: RegionKey, _photo: ImageTk.PhotoImage) -> int:
    return key[5] * key[6] * 4


def _byte_length(_key: object, data: bytes) -> int:
    return len(data)


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
    ZOOM_IDLE_DELAY_MS = 400
    WHEEL_STEP = 60
    DRAWN_CACHE_BYTES = 24 * 1024 * 1024
    PHOTO_CACHE_BYTES = 64 * 1024 * 1024
    MAX_RENDER_WORKERS = 2
    IMMEDIATE_RENDER_COUNT = 2
    RENDER_STEP_DELAY_MS = 1
    PRELOAD_DELAY_MS = 150
    PRELOAD_STEP_DELAY_MS = 10
    PRELOAD_DISTANCE = 3
    TILE_SIZE = 1024
    REGION_GRANULARITY = 128
    REGION_OVERSCAN = 128
    HORIZONTAL_REGION_OVERSCAN = 320
    VERTICAL_REGION_OVERSCAN = 320
    MAX_RENDER_ATTEMPTS = 3
    MEMORY_TRIM_DELAY_MS = 250
    CONTROL_MASK = 0x0004
    PAGE_GAP_SIZE = 12
    is_folder = True
    _render_generation = 0

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
        remember_folder: bool = True,
        prevent_image_upscale: bool = False,
        stop_at_fit_width: bool = True,
        is_folder: bool = True,
    ) -> None:
        self.window = window
        self.canvas: tk.Canvas = window["-CANVAS-"].TKCanvas
        self.pages = pages
        self.folder = folder
        self.is_folder = is_folder
        self.desktop_width = desktop_width
        self.viewport_width = max(1, self.canvas.winfo_width())
        self.viewport_height = max(1, self.canvas.winfo_height())
        self.prevent_image_upscale = prevent_image_upscale
        self.stop_at_fit_width = stop_at_fit_width
        self.dual_page = dual_page
        self.manga_reading = manga_reading
        self.page_spacing = page_spacing
        self.detect_double_spreads = detect_double_spreads
        self.remember_folder = remember_folder
        self.double_spread_indices: set[int] = set()
        self.typical_page_ratio = 1.0
        self._refresh_spread_analysis()
        self.original_size = False
        initial_width = max(1, round(desktop_width * self.START_WIDTH_RATIO))
        self.strip_width = min(initial_width, self._maximum_strip_width())
        self.scroll_y = 0
        self.pan_x = 0
        self.positions: list[PagePosition] = []
        self.drawn_webp_cache = MemoryShelf(self.DRAWN_CACHE_BYTES, _byte_length)
        self.ready_photos = MemoryShelf(self.PHOTO_CACHE_BYTES, _photo_bytes)
        self.canvas_pages: dict[Path, dict[RegionKey, CanvasTile]] = {}
        self.canvas_zones: dict[Path, int] = {}
        self.page_thumbnails: dict[Path, Image.Image] = {}
        self._preview_pages: set[Path] = set()
        self._preview_regions: set[RegionKey] = set()
        self._failed_regions: dict[RegionKey, int] = {}
        self.should_close = False
        self.fullscreen = False
        self._windowed_geometry = self.window.TKroot.geometry()
        self._windowed_maximized = self._is_maximized()
        self._resize_job: str | None = None
        self._zoom_job: str | None = None
        self._zoom_cleanup_job: str | None = None
        self._render_job: str | None = None
        self._preload_job: str | None = None
        self._memory_trim_job: str | None = None
        self._async_poll_job: str | None = None
        self._render_generation = 0
        pdf_workers = (
            1
            if not is_folder and folder.suffix.casefold() in PDF_SUFFIXES
            else None
        )
        self.async_renderer = AsyncRenderer(
            max_workers=pdf_workers or self.MAX_RENDER_WORKERS
        )
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

    @property
    def reading_zoom_level(self) -> str:
        """Return the displayed zoom in a compact, restorable form."""
        if self.original_size:
            return "original"
        return str(round(100 * self.strip_width / max(1, self.viewport_width)))

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
            "<plus>": lambda: self.zoom(1),
            "<minus>": lambda: self.zoom(-1),
            "<KP_Add>": lambda: self.zoom(1),
            "<KP_Subtract>": lambda: self.zoom(-1),
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
        if self.should_close:
            return
        self.should_close = True
        self.stop_zooming()
        if getattr(self, "async_renderer", None) is not None:
            self.async_renderer.shutdown()
        self._clear_thumbnails()

    def _clear_thumbnails(self) -> None:
        """Close and discard cached full-page previews."""
        for thumb in getattr(self, "page_thumbnails", {}).values():
            try:
                thumb.close()
            except Exception:
                pass
        if hasattr(self, "page_thumbnails"):
            self.page_thumbnails.clear()

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

    def _region_axis(
        self,
        visible_start: int,
        visible_end: int,
        page_size: int,
        viewport_size: int,
        overscan: int | None = None,
    ) -> tuple[int, int]:
        granularity = self.REGION_GRANULARITY
        margin = self.REGION_OVERSCAN if overscan is None else max(0, overscan)
        desired_span = viewport_size + 2 * margin
        rounded_span = (
            (desired_span + granularity - 1) // granularity
        ) * granularity
        span = min(page_size, rounded_span)
        if span >= page_size:
            return 0, page_size
        center = (visible_start + visible_end) // 2
        origin = ((center - span // 2) // granularity) * granularity
        origin = min(max(origin, 0), page_size - span)
        if origin > visible_start:
            origin = visible_start
        if origin + span < visible_end:
            origin = visible_end - span
        return origin, span

    def _region_keys(
        self,
        page: ComicPage,
        position: PagePosition,
        *,
        vertical_edge: str | None = None,
    ) -> list[RegionKey]:
        """Return stable fixed-size tiles covering the viewport and a small margin."""
        horizontal_overscan = max(
            0,
            getattr(self, "HORIZONTAL_REGION_OVERSCAN", self.REGION_OVERSCAN),
        )
        screen_x = position.x + self.pan_x
        visible_left = max(0, -screen_x)
        visible_right = min(position.width, self.viewport_width - screen_x)
        if visible_right <= visible_left:
            return []

        if vertical_edge == "top":
            visible_top = 0
            visible_bottom = min(position.height, self.TILE_SIZE)
        elif vertical_edge == "bottom":
            visible_bottom = position.height
            visible_top = max(0, visible_bottom - self.TILE_SIZE)
        else:
            screen_y = position.y - self.scroll_y
            visible_top = max(0, -screen_y)
            visible_bottom = min(position.height, self.viewport_height - screen_y)
            if visible_bottom <= visible_top:
                return []

        vertical_overscan = max(
            0,
            getattr(self, "VERTICAL_REGION_OVERSCAN", self.REGION_OVERSCAN),
        )
        left = max(0, visible_left - horizontal_overscan)
        right = min(position.width, visible_right + horizontal_overscan)
        top = max(0, visible_top - vertical_overscan)
        bottom = min(position.height, visible_bottom + vertical_overscan)
        tile_size = max(1, getattr(self, "TILE_SIZE", 1024))
        first_x = (left // tile_size) * tile_size
        first_y = (top // tile_size) * tile_size

        keys: list[RegionKey] = []
        for tile_top in range(first_y, bottom, tile_size):
            tile_height = min(tile_size, position.height - tile_top)
            for tile_left in range(first_x, right, tile_size):
                tile_width = min(tile_size, position.width - tile_left)
                keys.append(
                    (
                        page.file,
                        position.width,
                        position.height,
                        tile_left,
                        tile_top,
                        tile_width,
                        tile_height,
                    )
                )
        return keys

    def _region_key(
        self,
        page: ComicPage,
        position: PagePosition,
        *,
        vertical_edge: str | None = None,
    ) -> RegionKey | None:
        """Return the first visible tile for compatibility with focused callers."""
        keys = self._region_keys(page, position, vertical_edge=vertical_edge)
        return keys[0] if keys else None

    @staticmethod
    def _source_box(
        page: ComicPage, key: RegionKey
    ) -> tuple[float, float, float, float]:
        _, display_width, display_height, left, top, width, height = key
        scale_x = page.native_width / display_width
        scale_y = page.native_height / display_height
        return (
            left * scale_x,
            top * scale_y,
            (left + width) * scale_x,
            (top + height) * scale_y,
        )

    def _region_covers_visible_area(
        self, region: RegionKey, position: PagePosition
    ) -> bool:
        """Return whether a rendered crop covers the page area now on screen."""
        if region[1:3] != (position.width, position.height):
            return False

        screen_x = position.x + self.pan_x
        screen_y = position.y - self.scroll_y
        visible_left = max(0, -screen_x)
        visible_top = max(0, -screen_y)
        visible_right = min(position.width, self.viewport_width - screen_x)
        visible_bottom = min(position.height, self.viewport_height - screen_y)
        if visible_right <= visible_left or visible_bottom <= visible_top:
            return False

        left, top, width, height = region[3:]
        return (
            left <= visible_left
            and top <= visible_top
            and left + width >= visible_right
            and top + height >= visible_bottom
        )

    def _render_region(self, page: ComicPage, key: RegionKey) -> Image.Image | None:
        return render_page_region(
            page.file,
            self._source_box(page, key),
            (key[5], key[6]),
        )

    def _update_thumbnail(
        self, page_file: Path, image: Image.Image, key: RegionKey
    ) -> None:
        """Update or cache a low-resolution thumbnail from a rendered image."""
        if page_file in self.page_thumbnails or image is None:
            return
        _, display_width, display_height, left, top, width, height = key
        if (left, top, width, height) != (0, 0, display_width, display_height):
            return
        try:
            thumb = image.copy()
            thumb.thumbnail((200, 260), Image.Resampling.BILINEAR)
            thumb.load()
            self.page_thumbnails[page_file] = thumb
            if len(self.page_thumbnails) > 64:
                oldest_file = next(iter(self.page_thumbnails))
                old_thumb = self.page_thumbnails.pop(oldest_file)
                try:
                    old_thumb.close()
                except Exception:
                    pass
        except Exception:
            pass

    def _get_blurred_preview_photo(
        self, page: ComicPage, key: RegionKey
    ) -> ImageTk.PhotoImage | None:
        """Create a smooth blurred placeholder photo matching the exact region bounds."""
        thumb = self.page_thumbnails.get(page.file)
        if thumb is None:
            thumb = get_page_thumbnail(page.file)
            if thumb is not None:
                self.page_thumbnails[page.file] = thumb
                if len(self.page_thumbnails) > 64:
                    oldest_file = next(iter(self.page_thumbnails))
                    old_thumb = self.page_thumbnails.pop(oldest_file)
                    try:
                        old_thumb.close()
                    except Exception:
                        pass

        if thumb is None or thumb.width < 1 or thumb.height < 1:
            return None

        _, display_width, display_height, left, top, width, height = key
        if display_width < 1 or display_height < 1 or width < 1 or height < 1:
            return None

        tw, th = thumb.width, thumb.height
        box = (
            max(0.0, left / display_width * tw),
            max(0.0, top / display_height * th),
            min(float(tw), (left + width) / display_width * tw),
            min(float(th), (top + height) / display_height * th),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None

        try:
            preview = thumb.resize(
                (width, height), box=box, resample=Image.Resampling.BILINEAR
            )
            try:
                photo = ImageTk.PhotoImage(preview, master=self.canvas)
                return photo
            finally:
                preview.close()
        except Exception:
            return None

    def _store_drawn_webp(self, key: RegionKey, rendered: Image.Image) -> bytes:
        buf = io.BytesIO()
        rendered.save(buf, format="WEBP", quality=85, method=0)
        webp_bytes = buf.getvalue()
        self.drawn_webp_cache.store(key, webp_bytes)
        return webp_bytes

    def _ensure_drawn_webp(self, page: ComicPage, cache_key: RegionKey) -> bytes | None:
        cached = self.drawn_webp_cache.get(cache_key)
        if cached is not None:
            return cached

        rendered = self._render_region(page, cache_key)
        if rendered is None:
            return None
        try:
            self._update_thumbnail(page.file, rendered, cache_key)
            return self._store_drawn_webp(cache_key, rendered)
        finally:
            rendered.close()

    def _canvas_photo(
        self, page: ComicPage, cache_key: RegionKey
    ) -> ImageTk.PhotoImage | None:
        cached = self.ready_photos.get(cache_key)
        if cached is not None:
            return cached

        webp_bytes = self.drawn_webp_cache.get(cache_key)
        if webp_bytes is not None:
            with Image.open(io.BytesIO(webp_bytes)) as rendered:
                photo = ImageTk.PhotoImage(rendered, master=self.canvas)
                self._update_thumbnail(page.file, rendered, cache_key)
        else:
            rendered = self._render_region(page, cache_key)
            if rendered is None:
                return None
            try:
                photo = ImageTk.PhotoImage(rendered, master=self.canvas)
                self._store_drawn_webp(cache_key, rendered)
                self._update_thumbnail(page.file, rendered, cache_key)
            finally:
                rendered.close()
        self.ready_photos.store(cache_key, photo)
        return photo

    def _schedule_memory_trim(self) -> None:
        if getattr(self, "_memory_trim_job", None) is not None:
            self.canvas.after_cancel(self._memory_trim_job)
        self._memory_trim_job = self.canvas.after(
            self.MEMORY_TRIM_DELAY_MS, self._trim_unused_memory
        )

    def _trim_unused_memory(self) -> None:
        self._memory_trim_job = None
        trim_memory()

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

    def restore_zoom_level(self, zoom_level: str) -> None:
        """Restore the global zoom shared by every folder and window."""
        self.stop_zooming()
        if zoom_level == "original":
            self.show_original_size()
        else:
            try:
                percentage = max(1, int(zoom_level))
            except (TypeError, ValueError):
                percentage = round(
                    100 * self.strip_width / max(1, self.viewport_width)
                )
            minimum = min(
                self._maximum_strip_width(),
                max(1, round(self.desktop_width * self.MIN_WIDTH_RATIO)),
            )
            target_width = round(self.viewport_width * percentage / 100)
            target_width = min(
                max(target_width, minimum), self._maximum_strip_width()
            )
            self._set_strip_width(target_width, self.viewport_height // 2)

    def restore_reading_position(self, page_number: int, zoom_level: str) -> None:
        """Restore a zoom and page pair retained for API compatibility."""
        self.restore_zoom_level(zoom_level)
        self.go_to_page(page_number - 1)

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

        if getattr(self, "_zoom_cleanup_job", None) is not None:
            self.canvas.after_cancel(self._zoom_cleanup_job)
            self._zoom_cleanup_job = None

        # A new direction supersedes queued work. Repeated commands in the same
        # direction join the stash and are applied one at a time.
        if getattr(self, "_zoom_stash", None) and self._zoom_stash[-1][0] != direction:
            self.stop_zooming()
        else:
            self._cancel_pending_work()
        if getattr(self, "_zoom_stash", None) is not None:
            self._zoom_stash.extend((direction, anchor) for _ in range(abs(steps)))
        if getattr(self, "_zoom_job", None) is None:
            self._zoom_job = self.canvas.after(
                0, self._apply_next_zoom_step
            )

    def _apply_next_zoom_step(self) -> None:
        self._zoom_job = None
        if not getattr(self, "_zoom_stash", None):
            self._schedule_zoom_cleanup()
            return
        direction, anchor = self._zoom_stash.popleft()
        if not self._apply_zoom_step(direction, anchor, is_interactive=True):
            self._zoom_stash.clear()
            self._schedule_zoom_cleanup()
            return
        if self._zoom_stash:
            self._zoom_job = self.canvas.after(
                self.ZOOM_STEP_DELAY_MS, self._apply_next_zoom_step
            )
        else:
            self._schedule_zoom_cleanup()

    def _schedule_zoom_cleanup(self) -> None:
        if getattr(self, "_zoom_cleanup_job", None) is not None:
            self.canvas.after_cancel(self._zoom_cleanup_job)
            self._zoom_cleanup_job = None
        self._zoom_cleanup_job = self.canvas.after(
            self.ZOOM_IDLE_DELAY_MS, self._finish_sequential_zoom
        )

    def _finish_sequential_zoom(self) -> None:
        self._zoom_cleanup_job = None
        if self.pages and self.positions:
            self.paint()
        self._schedule_memory_trim()

    def _apply_zoom_step(
        self, direction: int, anchor: int, is_interactive: bool = False
    ) -> bool:
        maximum = self._maximum_strip_width()
        minimum = min(
            maximum,
            max(1, round(self.desktop_width * self.MIN_WIDTH_RATIO)),
        )
        requested = round(self.strip_width * (self.ZOOM_FACTOR**direction))
        new_width = min(max(requested, minimum), maximum)
        return self._set_strip_width(new_width, anchor, is_interactive=is_interactive)

    def _set_strip_width(
        self, new_width: int, anchor: int, is_interactive: bool = False
    ) -> bool:
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
        self._render_generation = getattr(self, "_render_generation", 0) + 1
        if getattr(self, "async_renderer", None) is not None:
            self.async_renderer.set_current_generation(self._render_generation)
        self.paint(priority_y=anchor, is_interactive=is_interactive)
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
        self._render_generation = getattr(self, "_render_generation", 0) + 1
        if getattr(self, "async_renderer", None) is not None:
            self.async_renderer.set_current_generation(self._render_generation)
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

    def open_bookshelf(
        self, pages: list[ComicPage], folder: Path, *, is_folder: bool = True
    ) -> None:
        self.stop_zooming()
        if getattr(self, "async_renderer", None) is not None:
            self.async_renderer.shutdown()
        self._clear_thumbnails()
        zoom_level = self.reading_zoom_level
        self.pages = pages
        self.folder = folder
        self.is_folder = is_folder
        pdf_workers = (
            1
            if not is_folder and folder.suffix.casefold() in PDF_SUFFIXES
            else None
        )
        self.async_renderer = AsyncRenderer(
            max_workers=pdf_workers or self.MAX_RENDER_WORKERS
        )
        self._render_generation += 1
        self.async_renderer.set_current_generation(self._render_generation)
        self._failed_regions.clear()
        self.window.set_title(
            reader_window_title(folder)
            if is_folder
            else reader_window_title(folder, len(pages))
        )
        self._refresh_spread_analysis()
        self.original_size = False
        initial_width = max(1, round(self.viewport_width * self.START_WIDTH_RATIO))
        self.strip_width = min(initial_width, self._maximum_strip_width())
        self.scroll_y = 0
        self.pan_x = 0
        self.drawn_webp_cache.clear()
        self.ready_photos.clear()
        self._clear_canvas()
        self._schedule_memory_trim()
        self._arrange_strip()
        self.paint()
        self.restore_zoom_level(zoom_level)
        self._show_status()

    def stop_zooming(self) -> None:
        """Discard queued zoom steps and rendering left by the last step."""
        if getattr(self, "_zoom_job", None) is not None:
            self.canvas.after_cancel(self._zoom_job)
            self._zoom_job = None
        if getattr(self, "_zoom_cleanup_job", None) is not None:
            self.canvas.after_cancel(self._zoom_cleanup_job)
            self._zoom_cleanup_job = None
        if getattr(self, "_zoom_stash", None) is not None:
            self._zoom_stash.clear()
        self._cancel_pending_work()
        self._schedule_memory_trim()

    def _clear_canvas(self) -> None:
        self._cancel_pending_work()
        for tiles in self.canvas_pages.values():
            for item, _photo, _size in tiles.values():
                self.canvas.delete(item)
        self.canvas_pages.clear()
        for item in self.canvas_zones.values():
            self.canvas.delete(item)
        self.canvas_zones.clear()
        self._preview_pages.clear()
        if hasattr(self, "_preview_regions"):
            self._preview_regions.clear()

    def _cancel_pending_work(self) -> None:
        if getattr(self, "_async_poll_job", None) is not None:
            self.canvas.after_cancel(self._async_poll_job)
            self._async_poll_job = None
        if getattr(self, "async_renderer", None) is not None:
            self.async_renderer.cancel_all()
        if getattr(self, "_render_job", None) is not None:
            self.canvas.after_cancel(self._render_job)
            self._render_job = None
        if getattr(self, "_preload_job", None) is not None:
            self.canvas.after_cancel(self._preload_job)
            self._preload_job = None
        if hasattr(self, "_pending_render_indices"):
            self._pending_render_indices.clear()
        if hasattr(self, "_pending_preload_indices"):
            self._pending_preload_indices.clear()

    def _refresh_preview_page(self, page_file: Path) -> None:
        tiles = self.canvas_pages.get(page_file, {})
        if any(key in self._preview_regions for key in tiles):
            self._preview_pages.add(page_file)
        else:
            self._preview_pages.discard(page_file)

    def _place_canvas_tile(
        self,
        page: ComicPage,
        position: PagePosition,
        key: RegionKey,
        photo: ImageTk.PhotoImage,
        *,
        preview: bool,
    ) -> None:
        tiles = self.canvas_pages.setdefault(page.file, {})
        existing = tiles.pop(key, None)
        if existing is not None:
            self.canvas.delete(existing[0])
        item = self.canvas.create_image(
            position.x + self.pan_x + key[3],
            position.y - self.scroll_y + key[4],
            anchor="nw",
            image=photo,
            tags=("comic-page",),
        )
        tiles[key] = (item, photo, key)
        if preview:
            self._preview_regions.add(key)
        else:
            self._preview_regions.discard(key)
        self._refresh_preview_page(page.file)

    def _render_tile(
        self, page: ComicPage, position: PagePosition, region_key: RegionKey
    ) -> None:
        existing = self.canvas_pages.get(page.file, {}).get(region_key)
        if existing is not None and region_key not in self._preview_regions:
            self.canvas.coords(
                existing[0],
                position.x + self.pan_x + region_key[3],
                position.y - self.scroll_y + region_key[4],
            )
            return

        # Create the replacement before deleting the preview so a slow cache
        # decode cannot leave a visible gap.
        photo = self._canvas_photo(page, region_key)
        if photo is None:
            return
        self._place_canvas_tile(
            page, position, region_key, photo, preview=False
        )

    def _render_page(self, index: int) -> None:
        page = self.pages[index]
        position = self.positions[index]
        for region_key in self._region_keys(page, position):
            self._render_tile(page, position, region_key)

    def paint(
        self, priority_y: int | None = None, is_interactive: bool = False
    ) -> None:
        self._cancel_pending_work()
        if not hasattr(self, "canvas_zones"):
            self.canvas_zones = {}
        if not hasattr(self, "_preview_pages"):
            self._preview_pages = set()
        if not hasattr(self, "_preview_regions"):
            self._preview_regions = set()
        if not hasattr(self, "page_thumbnails"):
            self.page_thumbnails = {}
        if not hasattr(self, "_failed_regions"):
            self._failed_regions = {}

        first, last = visible_page_range(
            self.positions, self.scroll_y, self.viewport_height
        )
        wanted_regions = {
            self.pages[index].file: self._region_keys(
                self.pages[index], self.positions[index]
            )
            for index in range(first, last)
        }
        wanted_regions = {
            page_file: keys for page_file, keys in wanted_regions.items() if keys
        }
        visible_page_files = {
            self.pages[index].file for index in range(first, last)
        }
        wanted_keys = {key for keys in wanted_regions.values() for key in keys}

        # 1. Maintain background page placeholder zones for all visible pages
        for index in range(first, last):
            page = self.pages[index]
            position = self.positions[index]
            page_x = position.x + self.pan_x
            page_y = position.y - self.scroll_y
            page_x2 = page_x + position.width
            page_y2 = page_y + position.height
            if page.file in self.canvas_zones:
                self.canvas.coords(
                    self.canvas_zones[page.file],
                    page_x,
                    page_y,
                    page_x2,
                    page_y2,
                )
            else:
                zone_item = self.canvas.create_rectangle(
                    page_x,
                    page_y,
                    page_x2,
                    page_y2,
                    fill="#181818",
                    outline="#2c2c2c",
                    tags=("page-zone",),
                )
                self.canvas_zones[page.file] = zone_item

        for file in set(self.canvas_zones) - visible_page_files:
            zone_item = self.canvas_zones.pop(file)
            self.canvas.delete(zone_item)

        # 2. Position existing fixed tiles, or display thumbnail previews.
        async_renderer = getattr(self, "async_renderer", None)
        for index in range(first, last):
            page = self.pages[index]
            position = self.positions[index]
            for region_key in wanted_regions.get(page.file, []):
                existing = self.canvas_pages.get(page.file, {}).get(region_key)
                if existing is not None:
                    self.canvas.coords(
                        existing[0],
                        position.x + self.pan_x + region_key[3],
                        position.y - self.scroll_y + region_key[4],
                    )
                    if region_key not in self._preview_regions:
                        continue
                if (
                    self.ready_photos.get(region_key) is not None
                    or self.drawn_webp_cache.get(region_key) is not None
                    or async_renderer is None
                ):
                    self._render_tile(page, position, region_key)
                    continue
                if existing is None:
                    preview_photo = self._get_blurred_preview_photo(
                        page, region_key
                    )
                    if preview_photo is not None:
                        self._place_canvas_tile(
                            page,
                            position,
                            region_key,
                            preview_photo,
                            preview=True,
                        )

        # 3. Evict tiles that no longer intersect the viewport margin.
        evicted = False
        for page_file, tiles in list(self.canvas_pages.items()):
            for key in list(tiles):
                if key in wanted_keys:
                    continue
                item, _photo, _size = tiles.pop(key)
                self.canvas.delete(item)
                self._preview_regions.discard(key)
                evicted = True
            if not tiles:
                self.canvas_pages.pop(page_file, None)
            self._refresh_preview_page(page_file)

        # Tk photos are uncompressed. Retain only active viewport tiles.
        active_raw_keys = wanted_keys
        stale_raw_keys = [
            key for key in self.ready_photos.keys() if key not in active_raw_keys
        ]
        for key in stale_raw_keys:
            self.ready_photos.pop(key)
            evicted = True

        if evicted:
            self._schedule_memory_trim()

        # 4. Determine tiles needing high-resolution background rendering.
        viewport_anchor = (
            self.viewport_height // 2 if priority_y is None else priority_y
        )
        content_anchor = self.scroll_y + min(
            max(viewport_anchor, 0), self.viewport_height
        )
        cold_tiles: list[tuple[int, RegionKey]] = []
        for index in range(first, last):
            page = self.pages[index]
            position = self.positions[index]
            for key in wanted_regions.get(page.file, []):
                existing = self.canvas_pages.get(page.file, {}).get(key)
                if existing is not None and key not in self._preview_regions:
                    continue
                if (
                    self.ready_photos.get(key) is not None
                    or self.drawn_webp_cache.get(key) is not None
                    or async_renderer is None
                ):
                    self._render_tile(page, position, key)
                elif (
                    not is_interactive
                    and self._failed_regions.get(key, 0)
                    < self.MAX_RENDER_ATTEMPTS
                ):
                    cold_tiles.append((index, key))

        cold_tiles.sort(
            key=lambda entry: abs(
                self.positions[entry[0]].y
                + entry[1][4]
                + entry[1][6] // 2
                - content_anchor
            )
        )

        # 5. Submit cold tiles only after zooming has settled.
        if async_renderer is not None and cold_tiles:
            generation = getattr(self, "_render_generation", 0)
            for rank, (index, key) in enumerate(cold_tiles):
                page = self.pages[index]
                async_renderer.submit(
                    priority=rank,
                    generation=generation,
                    page_file=page.file,
                    region_key=key,
                    source_box=self._source_box(page, key),
                    target_size=(key[5], key[6]),
                )
            self._schedule_async_poll()

        self.canvas.tag_lower("comic-page")
        self.canvas.tag_lower("page-zone")
        self._visible_range = (first, last)
        if hasattr(self, "_pending_render_indices"):
            self._pending_render_indices.clear()
        if not is_interactive:
            self._schedule_neighbor_preload()
        if async_renderer is not None and async_renderer.has_pending_work:
            self._schedule_async_poll()
        self._sync_scrollbar()
        self._show_page_counter()
        # Tk likes to batch wheel-driven paints. Flushing idle work makes the
        # reader feel immediate without forcing a full event-loop update.
        self.canvas.update_idletasks()

    def _schedule_async_poll(self) -> None:
        if getattr(self, "_async_poll_job", None) is None:
            self._async_poll_job = self.canvas.after(10, self._process_async_results)

    def _process_async_results(self) -> None:
        self._async_poll_job = None
        if not hasattr(self, "_preview_regions"):
            self._preview_regions = set()
        async_renderer = getattr(self, "async_renderer", None)
        if async_renderer is None:
            return

        results = async_renderer.get_results()
        first, last = getattr(self, "_visible_range", (0, 0))
        visible_files = {
            self.pages[i].file: (self.pages[i], self.positions[i])
            for i in range(first, last)
            if i < len(self.pages) and i < len(self.positions)
        }

        any_canvas_updated = False
        current_generation = getattr(self, "_render_generation", 0)
        for res in results:
            if res.generation != current_generation:
                if res.image is not None:
                    try:
                        res.image.close()
                    except Exception:
                        pass
                continue

            if res.image is None:
                self._failed_regions[res.region_key] = (
                    self._failed_regions.get(res.region_key, 0) + 1
                )
                continue

            try:
                self._failed_regions.pop(res.region_key, None)
                if res.webp_bytes is not None:
                    self.drawn_webp_cache.store(res.region_key, res.webp_bytes)
                self._update_thumbnail(res.page_file, res.image, res.region_key)

                if res.page_file in visible_files:
                    page, position = visible_files[res.page_file]
                    current_keys = set(self._region_keys(page, position))
                    if res.region_key in current_keys:
                        photo = ImageTk.PhotoImage(res.image, master=self.canvas)
                        self.ready_photos.store(res.region_key, photo)
                        self._place_canvas_tile(
                            page,
                            position,
                            res.region_key,
                            photo,
                            preview=False,
                        )
                        any_canvas_updated = True
            finally:
                res.image.close()

        if any_canvas_updated:
            self.canvas.tag_lower("comic-page")
            self.canvas.tag_lower("page-zone")
            self.canvas.update_idletasks()

        if async_renderer.has_pending_work:
            self._schedule_async_poll()
        else:
            # Retry any visible tile that did not finish and has attempts left.
            needs_drawing = False
            for i in range(first, last):
                if i >= len(self.pages) or i >= len(self.positions):
                    continue
                p = self.pages[i]
                pos = self.positions[i]
                tiles = self.canvas_pages.get(p.file, {})
                for expected_key in self._region_keys(p, pos):
                    if (
                        expected_key not in tiles
                        or expected_key in self._preview_regions
                    ) and (
                        self._failed_regions.get(expected_key, 0)
                        < self.MAX_RENDER_ATTEMPTS
                    ):
                        needs_drawing = True
                        break
                if needs_drawing:
                    break
            if needs_drawing:
                self.paint()
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
        async_renderer = getattr(self, "async_renderer", None)
        if async_renderer is not None and nearby:
            generation = getattr(self, "_render_generation", 0)
            submitted = False
            request_rank = 0
            for rank, index in enumerate(nearby):
                page = self.pages[index]
                position = self.positions[index]
                edge = "bottom" if index < first else "top"
                for region_key in self._region_keys(
                    page, position, vertical_edge=edge
                ):
                    if (
                        self.drawn_webp_cache.get(region_key) is None
                        and self.ready_photos.get(region_key) is None
                        and self._failed_regions.get(region_key, 0)
                        < self.MAX_RENDER_ATTEMPTS
                    ):
                        submitted = async_renderer.submit(
                            priority=10 + rank + request_rank,
                            generation=generation,
                            page_file=page.file,
                            region_key=region_key,
                            source_box=self._source_box(page, region_key),
                            target_size=(region_key[5], region_key[6]),
                        ) or submitted
                        request_rank += 1
            if submitted or async_renderer.has_pending_work:
                self._schedule_async_poll()
            return

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
        first, _last = self._visible_range
        edge = "bottom" if index < first else "top"
        for region_key in self._region_keys(page, position, vertical_edge=edge):
            self._ensure_drawn_webp(page, region_key)
        if self._pending_preload_indices:
            self._preload_job = self.canvas.after(
                self.PRELOAD_STEP_DELAY_MS, self._warm_next_neighbor
            )

    def _show_status(self) -> None:
        if self.original_size:
            zoom = "Original"
        else:
            zoom = f"{round(100 * self.strip_width / max(1, self.viewport_width))}%"
        if self.is_folder:
            status_text = self.folder.name or str(self.folder)
        else:
            count = len(self.pages)
            if self.folder.suffix.casefold() in PDF_SUFFIXES:
                count_label = "1 page" if count == 1 else f"{count} pages"
            else:
                count_label = "1 image" if count == 1 else f"{count} images"
            status_text = f"{self.folder.name or str(self.folder)} ({count_label})"
        self.window["-STATUS-"].update(status_text)
        self.window[ZOOM_STATUS_KEY].update(zoom)

    def _show_page_counter(self) -> None:
        self.window[PAGE_COUNTER_KEY].update(value=f"Pages {self.page_counter_text}")
