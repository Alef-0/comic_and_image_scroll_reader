"""Virtualized, scrollable comic canvas."""

from collections.abc import Callable
from pathlib import Path
import tkinter as tk

import FreeSimpleGUI as sg
from PIL import Image, ImageTk

from .bookshelf import open_page
from .layout import arrange_pages, clamp_scroll, visible_page_range
from .memory import MemoryShelf
from .models import ComicPage, PagePosition
from .window import CANVAS_COLOR


PhotoKey = tuple[Path, int, int]
CanvasPage = tuple[int, ImageTk.PhotoImage, tuple[int, int]]


def _decoded_bytes(_file: Path, image: Image.Image) -> int:
    return image.width * image.height * len(image.getbands())


def _photo_bytes(key: PhotoKey, _photo: ImageTk.PhotoImage) -> int:
    return key[1] * key[2] * 4


class ComicStrip:
    """Manage page geometry, rendering, input, and image caches."""

    START_WIDTH_RATIO = 0.75
    MIN_WIDTH_RATIO = 0.10
    MAX_WIDTH_RATIO = 4.0
    ZOOM_FACTOR = 1.10
    WHEEL_STEP = 120
    SOURCE_CACHE_BYTES = 256 * 1024 * 1024
    PHOTO_CACHE_BYTES = 192 * 1024 * 1024
    PRELOAD_DELAY_MS = 150
    CONTROL_MASK = 0x0004

    def __init__(
        self,
        window: sg.Window,
        pages: list[ComicPage],
        folder: Path,
        desktop_width: int,
    ) -> None:
        self.window = window
        self.canvas: tk.Canvas = window["-CANVAS-"].TKCanvas
        self.pages = pages
        self.folder = folder
        self.desktop_width = desktop_width
        self.viewport_width = max(1, self.canvas.winfo_width())
        self.viewport_height = max(1, self.canvas.winfo_height())
        self.strip_width = max(1, round(desktop_width * self.START_WIDTH_RATIO))
        self.scroll_y = 0
        self.positions: list[PagePosition] = []
        self.source_images = MemoryShelf(self.SOURCE_CACHE_BYTES, _decoded_bytes)
        self.ready_photos = MemoryShelf(self.PHOTO_CACHE_BYTES, _photo_bytes)
        self.canvas_pages: dict[Path, CanvasPage] = {}
        self.should_close = False
        self.fullscreen = False
        self._windowed_geometry = self.window.TKroot.geometry()
        self._windowed_maximized = self._is_maximized()
        self._resize_job: str | None = None
        self._preload_job: str | None = None
        self._visible_range = (0, 0)
        self.canvas.configure(
            background=CANVAS_COLOR, borderwidth=0, highlightthickness=0
        )
        self._connect_controls()
        self._arrange_strip()
        self.paint()
        self._show_status()

    @property
    def content_height(self) -> int:
        return self.positions[-1].bottom if self.positions else 0

    @property
    def maximum_scroll(self) -> int:
        return max(0, self.content_height - self.viewport_height)

    def _connect_controls(self) -> None:
        self.canvas.bind("<MouseWheel>", self._wheel_moved)
        self.canvas.bind("<Button-4>", self._wheel_moved)
        self.canvas.bind("<Button-5>", self._wheel_moved)
        self.canvas.bind("<Configure>", self._canvas_resized)
        self.canvas.bind("<Button-1>", lambda _event: self.canvas.focus_set())

        one_screen = lambda: max(1, self.viewport_height - 50)
        shortcuts: dict[str, Callable[[], None]] = {
            "<Down>": lambda: self.scroll(self.WHEEL_STEP),
            "<j>": lambda: self.scroll(self.WHEEL_STEP),
            "<s>": lambda: self.scroll(self.WHEEL_STEP),
            "<Up>": lambda: self.scroll(-self.WHEEL_STEP),
            "<k>": lambda: self.scroll(-self.WHEEL_STEP),
            "<w>": lambda: self.scroll(-self.WHEEL_STEP),
            "<Next>": lambda: self.scroll(one_screen()),
            "<space>": lambda: self.scroll(one_screen()),
            "<Prior>": lambda: self.scroll(-one_screen()),
            "<Home>": lambda: self.scroll(-self.maximum_scroll),
            "<g>": lambda: self.scroll(-self.maximum_scroll),
            "<End>": lambda: self.scroll(self.maximum_scroll),
            "<G>": lambda: self.scroll(self.maximum_scroll),
            "<Control-plus>": lambda: self.zoom(1),
            "<Control-equal>": lambda: self.zoom(1),
            "<Control-minus>": lambda: self.zoom(-1),
            "<F11>": self.toggle_fullscreen,
            "<Escape>": self.request_close,
            "<q>": self.request_close,
            "<Q>": self.request_close,
        }
        for sequence, action in shortcuts.items():
            self.window.TKroot.bind(sequence, self._keyboard_action(action))
        self.canvas.focus_set()

    @staticmethod
    def _keyboard_action(action: Callable[[], None]) -> Callable[[tk.Event], str]:
        def handle(_event: tk.Event) -> str:
            action()
            return "break"

        return handle

    def request_close(self) -> None:
        self.should_close = True

    def _is_maximized(self) -> bool:
        try:
            return bool(self.window.TKroot.attributes("-zoomed"))
        except tk.TclError:
            return self.window.TKroot.state() == "zoomed"

    def toggle_fullscreen(self) -> None:
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
        self.viewport_width, self.viewport_height = size
        if self._resize_job is not None:
            self.canvas.after_cancel(self._resize_job)
        self._resize_job = self.canvas.after(40, self._finish_resize)

    def _finish_resize(self) -> None:
        self._resize_job = None
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
            self.zoom(direction * steps, anchor_y=int(event.y))
        else:
            self.scroll(-direction * steps * self.WHEEL_STEP)
        return "break"

    def _arrange_strip(self) -> None:
        self.positions = arrange_pages(
            self.pages, self.strip_width, self.viewport_width
        )
        self.scroll_y = clamp_scroll(
            self.scroll_y, self.content_height, self.viewport_height
        )

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
            working = original
            if width < original.width and height < original.height:
                reduction = min(original.width // width, original.height // height)
                if reduction >= 2:
                    working = original.reduce(reduction)
            rendered = working.resize((width, height), Image.Resampling.BILINEAR)

        photo = ImageTk.PhotoImage(rendered, master=self.canvas)
        self.ready_photos.store(cache_key, photo)
        return photo

    def scroll(self, pixels: int) -> None:
        target = clamp_scroll(
            self.scroll_y + pixels, self.content_height, self.viewport_height
        )
        if target != self.scroll_y:
            self.scroll_y = target
            self.paint()

    def zoom(self, steps: int, anchor_y: int | None = None) -> None:
        if steps == 0:
            return
        anchor = self.viewport_height // 2 if anchor_y is None else anchor_y
        anchor = min(max(anchor, 0), self.viewport_height)
        old_height = max(1, self.content_height)
        reading_position = (self.scroll_y + anchor) / old_height
        minimum = max(1, round(self.desktop_width * self.MIN_WIDTH_RATIO))
        maximum = max(minimum, round(self.desktop_width * self.MAX_WIDTH_RATIO))
        requested = round(self.strip_width * (self.ZOOM_FACTOR**steps))
        new_width = min(max(requested, minimum), maximum)
        if new_width == self.strip_width:
            return

        self.strip_width = new_width
        self._clear_canvas()
        self._arrange_strip()
        anchored_scroll = round(reading_position * self.content_height - anchor)
        self.scroll_y = clamp_scroll(
            anchored_scroll, self.content_height, self.viewport_height
        )
        self.paint()
        self._show_status()

    def fit_width(self) -> None:
        if self.strip_width == self.viewport_width:
            return
        old_height = max(1, self.content_height)
        reading_position = (self.scroll_y + self.viewport_height / 2) / old_height
        self.strip_width = self.viewport_width
        self._clear_canvas()
        self._arrange_strip()
        target = round(
            reading_position * self.content_height - self.viewport_height / 2
        )
        self.scroll_y = clamp_scroll(target, self.content_height, self.viewport_height)
        self.paint()
        self._show_status()

    def open_bookshelf(self, pages: list[ComicPage], folder: Path) -> None:
        self.pages = pages
        self.folder = folder
        self.strip_width = max(1, round(self.viewport_width * self.START_WIDTH_RATIO))
        self.scroll_y = 0
        self.source_images.clear()
        self.ready_photos.clear()
        self._clear_canvas()
        self._arrange_strip()
        self.paint()
        self._show_status()

    def _clear_canvas(self) -> None:
        for item, _photo, _size in self.canvas_pages.values():
            self.canvas.delete(item)
        self.canvas_pages.clear()

    def paint(self) -> None:
        if self._preload_job is not None:
            self.canvas.after_cancel(self._preload_job)
            self._preload_job = None

        first, last = visible_page_range(
            self.positions, self.scroll_y, self.viewport_height
        )
        wanted_files = {self.pages[index].file for index in range(first, last)}
        for index in range(first, last):
            page = self.pages[index]
            position = self.positions[index]
            size = (position.width, position.height)
            existing = self.canvas_pages.get(page.file)
            if existing is None or existing[2] != size:
                if existing is not None:
                    self.canvas.delete(existing[0])
                photo = self._canvas_photo(page, *size)
                if photo is None:
                    continue
                item = self.canvas.create_image(
                    position.x,
                    position.y - self.scroll_y,
                    anchor="nw",
                    image=photo,
                    tags=("comic-page",),
                )
                self.canvas_pages[page.file] = (item, photo, size)
            else:
                self.canvas.coords(existing[0], position.x, position.y - self.scroll_y)

        for file in set(self.canvas_pages) - wanted_files:
            item, _photo, _size = self.canvas_pages.pop(file)
            self.canvas.delete(item)
        self.canvas.tag_lower("comic-page")
        self._visible_range = (first, last)
        self._preload_job = self.canvas.after(
            self.PRELOAD_DELAY_MS, self._warm_neighbor_pages
        )
        # Tk likes to batch wheel-driven paints. Flushing idle work makes the
        # reader feel immediate without forcing a full event-loop update.
        self.canvas.update_idletasks()

    def _warm_neighbor_pages(self) -> None:
        self._preload_job = None
        first, last = self._visible_range
        for index in (first - 1, last):
            if 0 <= index < len(self.pages):
                page = self.pages[index]
                position = self.positions[index]
                self._canvas_photo(page, position.width, position.height)

    def _show_status(self) -> None:
        zoom = round(100 * self.strip_width / max(1, self.viewport_width))
        self.window["-STATUS-"].update(
            f"{self.folder.name}  •  {len(self.pages)} pages  •  {zoom}%"
        )
