"""Optional Qt frontend for comparing image decode and display performance."""

from __future__ import annotations

import argparse
from collections import OrderedDict
from collections.abc import Sequence
import math
from pathlib import Path
import re
from statistics import median
import sys

from PySide6.QtCore import (
    QObject,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QImage,
    QImageReader,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import CONFIG_PATH, load_config, save_config
from .core.layout import arrange_pages, detect_double_spread_indices, visible_page_range
from .core.models import ComicPage, PagePosition
from .files.bookshelf import (
    IMAGE_SUFFIXES,
    PDF_SUFFIXES,
    load_pages,
    render_page_region,
    scan_bookshelf,
)
from .files.pdf_reader import PdfBookshelf, ensure_page_available
from .reading_progress import ReadingProgress, progress_for_folder, save_reading_progress


class DecodeSignals(QObject):
    """Deliver a worker's QImage back to the GUI thread."""

    finished = Signal(int, int, str, QImage, str)


class DecodeTask(QRunnable):
    """Decode one display-sized image without blocking Qt's event loop."""

    def __init__(
        self,
        revision: int,
        page_index: int,
        tier: str,
        page: ComicPage,
        target_size: QSize,
    ) -> None:
        super().__init__()
        self.revision = revision
        self.page_index = page_index
        self.tier = tier
        self.page = page
        self.target_size = target_size
        self.signals = DecodeSignals()

    def run(self) -> None:
        try:
            if not self.page.file.is_file():
                ensure_page_available(self.page.file)
        except Exception as error:
            self.signals.finished.emit(
                self.revision,
                self.page_index,
                self.tier,
                QImage(),
                str(error),
            )
            return
        reader = QImageReader(str(self.page.file))
        reader.setAutoTransform(True)

        # QImageReader applies scaling during decode when the format plugin can.
        # Account for EXIF orientations that swap width and height.
        source_size = reader.size()
        decode_size = self.target_size
        if (
            source_size.isValid()
            and source_size.width() == self.page.native_height
            and source_size.height() == self.page.native_width
        ):
            decode_size = QSize(self.target_size.height(), self.target_size.width())
        reader.setScaledSize(decode_size)
        image = reader.read()
        error = reader.errorString() if image.isNull() else ""
        if image.isNull():
            # Keep the main reader's full format coverage when a particular Qt
            # installation does not provide an image plugin for that suffix.
            rendered = render_page_region(
                self.page.file,
                (0.0, 0.0, self.page.native_width, self.page.native_height),
                (self.target_size.width(), self.target_size.height()),
            )
            if rendered is not None:
                try:
                    from PIL.ImageQt import ImageQt

                    image = QImage(ImageQt(rendered)).copy()
                    error = ""
                finally:
                    rendered.close()
        if not image.isNull() and image.size() != self.target_size:
            image = image.scaled(
                self.target_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        self.signals.finished.emit(
            self.revision,
            self.page_index,
            self.tier,
            image,
            error,
        )


class QtComicView(QGraphicsView):
    """Virtualized continuous image strip backed by display-sized Qt pixmaps."""

    pageChanged = Signal(str)
    zoomChanged = Signal(str)
    pathsDropped = Signal(list)
    closeRequested = Signal()
    fullscreenRequested = Signal()

    START_WIDTH_RATIO = 0.75
    MIN_WIDTH_RATIO = 0.10
    MAX_WIDTH_RATIO = 4.0
    ZOOM_FACTOR = 1.10
    PAGE_GAP_SIZE = 12
    DECODE_IDLE_MS = 250
    MAX_PIXMAP_PIXELS = 8 * 1024 * 1024
    PIXMAP_CACHE_BYTES = 96 * 1024 * 1024
    BASE_PIXMAP_CACHE_BYTES = 64 * 1024 * 1024
    MAX_BASE_PIXELS = 192 * 1024
    MIN_BASE_PIXELS = 4 * 1024
    PRELOAD_DISTANCE = 1
    SCROLL_STEP = 60

    def __init__(self, config: dict[str, object], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.pages: list[ComicPage] = []
        self.positions: list[PagePosition] = []
        self.folder = Path.cwd()
        self.is_folder = True
        self.prevent_image_upscale = bool(config["prevent_image_upscale"])
        self.stop_at_fit_width = bool(config["stop_at_fit_width"])
        self.dual_page = bool(config["dual_page"])
        self.manga_reading = bool(config["manga_reading"])
        self.page_spacing = bool(config["page_spacing"])
        self.detect_double_spreads = bool(config["detect_double_spreads"])
        self.double_spread_indices: set[int] = set()
        self.original_size = False
        self.strip_width = 1

        self._pixmap_items: list[QGraphicsPixmapItem] = []
        self._placeholders: list[QGraphicsRectItem] = []
        self._base_pixmaps: dict[int, QPixmap] = {}
        self._base_pixmap_bytes: dict[int, int] = {}
        self._decoded_sizes: dict[int, QSize] = {}
        self._pixmap_bytes: OrderedDict[int, int] = OrderedDict()
        self._tasks: dict[tuple[str, int], DecodeTask] = {}
        self._failed_pages: set[int] = set()
        self._decode_revision = 0
        self._detail_revision = 0
        self._last_visible_range = (-1, -1)
        self._layout_ready = False

        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._decode_timer = QTimer(self)
        self._decode_timer.setSingleShot(True)
        self._decode_timer.setInterval(self.DECODE_IDLE_MS)
        self._decode_timer.timeout.connect(self._refresh_visible_decodes)
        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.timeout.connect(self._update_visible_pages)
        self.verticalScrollBar().valueChanged.connect(self._schedule_visible_update)
        self.horizontalScrollBar().valueChanged.connect(self._schedule_visible_update)

    @property
    def content_height(self) -> int:
        return max((position.bottom for position in self.positions), default=1)

    @property
    def current_page_number(self) -> int:
        _first, last = self._visible_range()
        return min(len(self.pages), last) if self.pages else 0

    @property
    def page_counter_text(self) -> str:
        """Describe the last visible page or its active paired-page row."""
        total = len(self.pages)
        if not total:
            return "0 / 0"
        last_index = max(0, self.current_page_number - 1)
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
        if self.original_size:
            return "original"
        return str(round(100 * self.strip_width / max(1, self.viewport().width())))

    def set_pages(self, pages: list[ComicPage], folder: Path, *, is_folder: bool) -> None:
        self._decode_revision += 1
        self._detail_revision += 1
        self._thread_pool.clear()
        self._tasks.clear()
        self.scene().clear()
        self.pages = pages
        self.folder = folder
        self.is_folder = is_folder
        self._pixmap_items = []
        self._placeholders = []
        self._base_pixmaps.clear()
        self._base_pixmap_bytes.clear()
        self._decoded_sizes.clear()
        self._pixmap_bytes.clear()
        self._failed_pages.clear()
        self._last_visible_range = (-1, -1)
        self._layout_ready = False
        self._refresh_spread_analysis()

        for page in pages:
            placeholder = QGraphicsRectItem()
            placeholder.setBrush(QColor("#303030"))
            placeholder.setPen(QPen(Qt.PenStyle.NoPen))
            self.scene().addItem(placeholder)
            self._placeholders.append(placeholder)

            item = QGraphicsPixmapItem()
            item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.scene().addItem(item)
            self._pixmap_items.append(item)

        desktop_width = max(1, QApplication.primaryScreen().availableGeometry().width())
        self.original_size = False
        self.strip_width = min(
            max(1, round(desktop_width * self.START_WIDTH_RATIO)),
            self._maximum_strip_width(),
        )
        self._layout_pages(preserve_position=False)
        self._schedule_decode_refresh()
        QTimer.singleShot(0, self._queue_base_decodes)

    def restore_zoom_level(self, value: str) -> None:
        if value == "original":
            self.show_original_size()
            return
        try:
            percentage = max(1, int(value))
        except (TypeError, ValueError):
            percentage = 75
        minimum = min(self._maximum_strip_width(), self._minimum_strip_width())
        self.original_size = False
        self.strip_width = min(
            max(round(self.viewport().width() * percentage / 100), minimum),
            self._maximum_strip_width(),
        )
        self._layout_pages()
        self._schedule_decode_refresh()

    def _refresh_spread_analysis(self) -> None:
        self.double_spread_indices = (
            detect_double_spread_indices(self.pages)
            if self.detect_double_spreads
            else set()
        )

    def _page_widths(self) -> list[int]:
        if self.original_size:
            return [self._original_page_width(index) for index in range(len(self.pages))]
        widths = [
            min(self.strip_width, page.native_width)
            if self.prevent_image_upscale
            else self.strip_width
            for page in self.pages
        ]
        regular_ratios = [
            page.native_width / page.native_height
            for index, page in enumerate(self.pages)
            if index not in self.double_spread_indices
        ]
        all_ratios = [page.native_width / page.native_height for page in self.pages]
        typical_ratio = median(regular_ratios or all_ratios or [1.0])
        target_height = self.strip_width / max(typical_ratio, 0.01)
        desktop_width = max(1, QApplication.primaryScreen().availableGeometry().width())
        for index in self.double_spread_indices:
            page = self.pages[index]
            width = round(target_height * page.native_width / page.native_height)
            width = min(width, round(desktop_width * self.MAX_WIDTH_RATIO))
            if self.stop_at_fit_width:
                width = min(width, self._fitted_page_width(1))
            if self.prevent_image_upscale:
                width = min(width, page.native_width)
            widths[index] = max(1, width)
        return widths

    def _capture_layout_anchor(
        self, anchor_y: int | None = None
    ) -> tuple[int, float, int] | None:
        """Remember a page-relative point so layout changes do not jump."""
        if not self._layout_ready or not self.positions:
            return None
        viewport_y = (
            self.viewport().height() // 2
            if anchor_y is None
            else min(max(anchor_y, 0), self.viewport().height())
        )
        scene_y = self.mapToScene(0, viewport_y).y()
        first, last = visible_page_range(self.positions, round(scene_y), 1)
        index = min(max(first, 0), len(self.positions) - 1)
        if first == last:
            index = min(
                range(len(self.positions)),
                key=lambda item: min(
                    abs(scene_y - self.positions[item].y),
                    abs(scene_y - self.positions[item].bottom),
                ),
            )
        position = self.positions[index]
        relative_y = (scene_y - position.y) / max(1, position.height)
        return index, min(max(relative_y, 0.0), 1.0), viewport_y

    def _layout_pages(
        self,
        *,
        preserve_position: bool = True,
        anchor_y: int | None = None,
    ) -> None:
        anchor = self._capture_layout_anchor(anchor_y) if preserve_position else None
        self.positions = arrange_pages(
            self.pages,
            self._page_widths(),
            max(1, self.viewport().width()),
            dual_page=self.dual_page,
            manga_reading=self.manga_reading,
            page_gap=self.PAGE_GAP_SIZE if self.page_spacing else 0,
            solo_page_indices=self.double_spread_indices,
        )
        left = min((position.x for position in self.positions), default=0)
        right = max((position.x + position.width for position in self.positions), default=1)
        shift_x = max(0, -left)
        scene_width = max(self.viewport().width(), right + shift_x)

        for index, position in enumerate(self.positions):
            x = position.x + shift_x
            self._placeholders[index].setRect(
                QRectF(x, position.y, position.width, position.height)
            )
            item = self._pixmap_items[index]
            item.setPos(x, position.y)
            pixmap = item.pixmap()
            if not pixmap.isNull():
                item.setTransform(
                    QTransform.fromScale(
                        position.width / pixmap.width(),
                        position.height / pixmap.height(),
                    )
                )
        self.scene().setSceneRect(QRectF(0, 0, scene_width, self.content_height))
        self._layout_ready = True
        if anchor is not None:
            index, relative_y, viewport_y = anchor
            index = min(index, len(self.positions) - 1)
            position = self.positions[index]
            target_y = position.y + relative_y * position.height
            self.verticalScrollBar().setValue(round(target_y - viewport_y))
        zoom_text = (
            f"{self.reading_zoom_level}%" if not self.original_size else "Original"
        )
        self.zoomChanged.emit(zoom_text)
        self._schedule_visible_update()

    def _visible_range(self) -> tuple[int, int]:
        scene_top = round(self.mapToScene(self.viewport().rect()).boundingRect().top())
        return visible_page_range(
            self.positions,
            max(0, scene_top),
            max(1, self.viewport().height()),
        )

    def _schedule_visible_update(self, _value: int | None = None) -> None:
        if not self._visible_timer.isActive():
            self._visible_timer.start(0)

    def _schedule_decode_refresh(self) -> None:
        self._decode_timer.start()
        self._schedule_visible_update()

    def _update_visible_pages(self) -> None:
        if not self.pages or not self.positions:
            return
        first, last = self._visible_range()
        visible = set(range(first, last))
        retained = set(visible)
        retained.update(range(max(0, first - self.PRELOAD_DISTANCE), first))
        retained.update(
            range(last, min(len(self.pages), last + self.PRELOAD_DISTANCE))
        )

        for index in visible:
            self._touch_pixmap(index)
        for index in sorted(retained):
            self._request_decode(index)
        self._evict_pixmaps(visible)

        visible_range = (first, last)
        if visible_range != self._last_visible_range:
            self._last_visible_range = visible_range
            self.pageChanged.emit(f"Pages {self.page_counter_text}")

    def _refresh_visible_decodes(self) -> None:
        self._detail_revision += 1
        for key in list(self._tasks):
            if key[0] == "detail":
                self._thread_pool.tryTake(self._tasks[key])
                self._tasks.pop(key, None)
        self._update_visible_pages()

    def _decode_size(self, index: int) -> QSize:
        page = self.pages[index]
        position = self.positions[index]
        width = min(page.native_width, max(1, position.width))
        height = min(page.native_height, max(1, position.height))
        pixels = width * height
        if pixels > self.MAX_PIXMAP_PIXELS:
            scale = math.sqrt(self.MAX_PIXMAP_PIXELS / pixels)
            width = max(1, round(width * scale))
            height = max(1, round(height * scale))
        return QSize(width, height)

    def _base_decode_size(self, index: int) -> QSize:
        page = self.pages[index]
        per_page_pixels = min(
            self.MAX_BASE_PIXELS,
            max(
                self.MIN_BASE_PIXELS,
                self.BASE_PIXMAP_CACHE_BYTES // max(1, 4 * len(self.pages)),
            ),
        )
        source_pixels = page.native_width * page.native_height
        scale = min(1.0, math.sqrt(per_page_pixels / max(1, source_pixels)))
        return QSize(
            max(1, round(page.native_width * scale)),
            max(1, round(page.native_height * scale)),
        )

    def _queue_base_decodes(self) -> None:
        """Build a small retained fallback for every page in the background."""
        for index in range(len(self.pages)):
            self._request_decode(index, tier="base")

    def _request_decode(self, index: int, *, tier: str = "detail") -> None:
        wanted = (
            self._base_decode_size(index)
            if tier == "base"
            else self._decode_size(index)
        )
        key = (tier, index)
        if (
            (tier == "base" and index in self._base_pixmaps)
            or (tier == "detail" and self._decoded_sizes.get(index) == wanted)
            or key in self._tasks
            or index in self._failed_pages
        ):
            return
        revision = (
            self._decode_revision if tier == "base" else self._detail_revision
        )
        task = DecodeTask(revision, index, tier, self.pages[index], wanted)
        task.signals.finished.connect(self._image_decoded)
        self._tasks[key] = task
        priority = -10 if tier == "base" else 100
        self._thread_pool.start(task, priority)

    def _image_decoded(
        self,
        revision: int,
        page_index: int,
        tier: str,
        image: QImage,
        error: str,
    ) -> None:
        key = (tier, page_index)
        task = self._tasks.get(key)
        if task is not None and task.revision == revision:
            self._tasks.pop(key, None)
        current_revision = (
            self._decode_revision if tier == "base" else self._detail_revision
        )
        if revision != current_revision or page_index >= len(self.pages):
            return
        if image.isNull():
            self._failed_pages.add(page_index)
            print(
                f"Warning: Qt could not decode {self.pages[page_index].file.name}: {error}",
                file=sys.stderr,
            )
            return

        pixmap = QPixmap.fromImage(image)
        if tier == "base":
            self._base_pixmaps[page_index] = pixmap
            self._base_pixmap_bytes[page_index] = image.sizeInBytes()
            if page_index not in self._decoded_sizes:
                self._show_pixmap(page_index, pixmap)
            return

        self._show_pixmap(page_index, pixmap)
        self._decoded_sizes[page_index] = image.size()
        self._pixmap_bytes[page_index] = image.sizeInBytes()
        self._touch_pixmap(page_index)
        first, last = self._visible_range()
        visible = set(range(first, last))
        self._evict_pixmaps(visible)

    def _show_pixmap(self, page_index: int, pixmap: QPixmap) -> None:
        item = self._pixmap_items[page_index]
        item.setPixmap(pixmap)
        position = self.positions[page_index]
        item.setTransform(
            QTransform.fromScale(
                position.width / max(1, pixmap.width()),
                position.height / max(1, pixmap.height()),
            )
        )

    def _touch_pixmap(self, index: int) -> None:
        if index in self._pixmap_bytes:
            self._pixmap_bytes.move_to_end(index)

    def _evict_pixmaps(self, protected: set[int]) -> None:
        while sum(self._pixmap_bytes.values()) > self.PIXMAP_CACHE_BYTES:
            victim = next(
                (index for index in self._pixmap_bytes if index not in protected),
                None,
            )
            if victim is None:
                break
            self._discard_pixmap(victim)

    def _discard_pixmap(self, index: int) -> None:
        base = self._base_pixmaps.get(index)
        if base is None:
            self._pixmap_items[index].setPixmap(QPixmap())
        else:
            self._show_pixmap(index, base)
        self._decoded_sizes.pop(index, None)
        self._pixmap_bytes.pop(index, None)

    def zoom(self, direction: int, anchor_y: int | None = None) -> None:
        if not self.pages or direction == 0:
            return
        minimum = min(self._maximum_strip_width(), self._minimum_strip_width())
        requested = round(self.strip_width * self.ZOOM_FACTOR ** (1 if direction > 0 else -1))
        new_width = min(max(requested, minimum), self._maximum_strip_width())
        if new_width == self.strip_width and not self.original_size:
            return
        self.original_size = False
        self.strip_width = new_width
        self._layout_pages(anchor_y=anchor_y)
        self._schedule_decode_refresh()

    def fit_width(self) -> None:
        self.original_size = False
        self.strip_width = self._fitted_page_width(2 if self.dual_page else 1)
        if self.prevent_image_upscale:
            self.strip_width = min(
                self.strip_width,
                max((page.native_width for page in self.pages), default=1),
            )
        self._layout_pages()
        self._schedule_decode_refresh()

    def show_original_size(self) -> None:
        self.original_size = True
        self._layout_pages()
        self._schedule_decode_refresh()

    def set_options(
        self,
        *,
        dual_page: bool,
        manga_reading: bool,
        page_spacing: bool,
        detect_double_spreads: bool,
        prevent_image_upscale: bool,
        stop_at_fit_width: bool,
    ) -> None:
        self.dual_page = dual_page
        self.manga_reading = manga_reading
        self.page_spacing = page_spacing
        self.detect_double_spreads = detect_double_spreads
        self.prevent_image_upscale = prevent_image_upscale
        self.stop_at_fit_width = stop_at_fit_width
        self._refresh_spread_analysis()
        if not self.original_size:
            self.strip_width = min(self.strip_width, self._maximum_strip_width())
        self._layout_pages()
        self._schedule_decode_refresh()

    def go_to_page_number(self, page_number: int) -> None:
        if not self.positions:
            return
        index = min(max(page_number - 1, 0), len(self.positions) - 1)
        self.verticalScrollBar().setValue(self.positions[index].y)

    def scroll_by(self, pixels: int) -> None:
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.value() + pixels)

    def pan_horizontally(self, pixels: int) -> None:
        scrollbar = self.horizontalScrollBar()
        scrollbar.setValue(scrollbar.value() + pixels)

    def shutdown(self) -> None:
        """Stop accepting results and let the active decoder leave cleanly."""
        self._decode_revision += 1
        self._detail_revision += 1
        self._decode_timer.stop()
        self._visible_timer.stop()
        self._thread_pool.clear()
        self._thread_pool.waitForDone()
        self._tasks.clear()

    def _fitted_page_width(self, columns: int) -> int:
        gap = self.PAGE_GAP_SIZE if columns > 1 and self.page_spacing else 0
        return max(1, (self.viewport().width() - gap) // columns)

    def _original_page_width(self, index: int) -> int:
        native_width = self.pages[index].native_width
        if not self.stop_at_fit_width:
            return native_width
        is_solo = index == 0 or index in self.double_spread_indices
        columns = 2 if self.dual_page and not is_solo else 1
        return min(native_width, self._fitted_page_width(columns))

    def _minimum_strip_width(self) -> int:
        desktop_width = max(1, QApplication.primaryScreen().availableGeometry().width())
        return max(1, round(desktop_width * self.MIN_WIDTH_RATIO))

    def _maximum_strip_width(self) -> int:
        desktop_width = max(1, QApplication.primaryScreen().availableGeometry().width())
        maximum = max(1, round(desktop_width * self.MAX_WIDTH_RATIO))
        if self.stop_at_fit_width:
            maximum = min(maximum, self._fitted_page_width(2 if self.dual_page else 1))
        if self.prevent_image_upscale:
            maximum = min(
                maximum,
                max((page.native_width for page in self.pages), default=1),
            )
        return max(1, maximum)

    def wheelEvent(self, event: object) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(event.angleDelta().y(), round(event.position().y()))
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta:
            steps = max(1, abs(delta) // 120)
            self.scroll_by((-1 if delta > 0 else 1) * steps * self.SCROLL_STEP)
            event.accept()
        else:
            super().wheelEvent(event)
        self._schedule_visible_update()

    def keyPressEvent(self, event: object) -> None:
        key = event.key()
        screen_step = max(1, self.viewport().height() - 50)

        if key in {Qt.Key.Key_Plus, Qt.Key.Key_Equal}:
            self.zoom(1)
        elif key == Qt.Key.Key_Minus:
            self.zoom(-1)
        elif key in {Qt.Key.Key_Down, Qt.Key.Key_J, Qt.Key.Key_S}:
            self.scroll_by(self.SCROLL_STEP)
        elif key in {Qt.Key.Key_Up, Qt.Key.Key_K, Qt.Key.Key_W}:
            self.scroll_by(-self.SCROLL_STEP)
        elif key == Qt.Key.Key_Left:
            self.pan_horizontally(-self.SCROLL_STEP)
        elif key == Qt.Key.Key_Right:
            self.pan_horizontally(self.SCROLL_STEP)
        elif key in {Qt.Key.Key_PageDown, Qt.Key.Key_Space}:
            self.scroll_by(screen_step)
        elif key == Qt.Key.Key_PageUp:
            self.scroll_by(-screen_step)
        elif key in {Qt.Key.Key_Home, Qt.Key.Key_G} and not (
            key == Qt.Key.Key_G and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.verticalScrollBar().setValue(0)
        elif key == Qt.Key.Key_End or (
            key == Qt.Key.Key_G
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        elif key == Qt.Key.Key_F11:
            self.fullscreenRequested.emit()
        elif key == Qt.Key.Key_Escape or key == Qt.Key.Key_Q:
            self.closeRequested.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        if self.pages:
            if self.stop_at_fit_width and not self.original_size:
                self.strip_width = min(self.strip_width, self._maximum_strip_width())
            self._layout_pages()
            self._schedule_decode_refresh()

    def dragEnterEvent(self, event: object) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: object) -> None:
        paths = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if paths:
            self.pathsDropped.emit(paths)
            event.acceptProposedAction()


class QtReaderWindow(QMainWindow):
    """Qt control shell with the established reader behavior and labels."""

    def __init__(
        self,
        config: dict[str, object],
        *,
        expand_all: bool = False,
    ) -> None:
        super().__init__()
        self.config = config
        self.remember_folder = bool(config["remember_folder"])
        self._active_pdf: PdfBookshelf | None = None
        self._retired_pdfs: list[PdfBookshelf] = []
        self._fullscreen_was_maximized = False
        self.setWindowTitle("Comic and Scroll Reader")
        icon_path = Path(__file__).resolve().parent / "assets" / "csr_app_icon.png"
        self.setWindowIcon(QIcon(str(icon_path)))

        self.view = QtComicView(config)
        self.status_label = QLabel("")
        self.status_label.setMaximumWidth(300)
        self.zoom_label = QLabel("75%")
        self.page_button = QToolButton()
        self.page_button.setText("Pages 0 / 0")
        self.page_button.setToolTip("Go to page")
        self.page_button.clicked.connect(self.ask_for_page_number)
        self._option_actions: dict[str, QAction] = {}

        self.toolbar = QToolBar("Reader controls", self)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)
        self._add_toolbar_action("Open Folder", self.open_folder)
        self._add_toolbar_action("Open Files", self.open_files)
        self._add_toolbar_action("Save Configs", self.save_preferences)

        image_size_menu = QMenu("Image size", self)
        self._add_menu_action(image_size_menu, "Fit Width", self.view.fit_width)
        self._add_menu_action(
            image_size_menu,
            "Original Size",
            self.view.show_original_size,
        )
        self._add_option_action(
            image_size_menu,
            "Don't enlarge images",
            "prevent_image_upscale",
        )
        self._add_option_action(
            image_size_menu,
            "Stop at fit width",
            "stop_at_fit_width",
        )

        page_layout_menu = QMenu("Page layout", self)
        self._add_option_action(page_layout_menu, "Dual page", "dual_page")
        self._add_option_action(
            page_layout_menu,
            "Manga order",
            "manga_reading",
        )
        self._add_option_action(
            page_layout_menu,
            "Page spacing",
            "page_spacing",
        )

        experimental_menu = QMenu("Experimental", self)
        self._add_option_action(
            experimental_menu,
            "Detect double-page spreads",
            "detect_double_spreads",
        )
        self._add_option_action(
            experimental_menu,
            "Remember folder",
            "remember_folder",
        )
        for title, menu in (
            ("Image size", image_size_menu),
            ("Page layout", page_layout_menu),
            ("Experimental", experimental_menu),
        ):
            if expand_all:
                self.toolbar.addSeparator()
                label = QLabel(f"{title}:")
                self.toolbar.addWidget(label)
                for action in menu.actions():
                    self.toolbar.addAction(action)
            else:
                button = QToolButton()
                button.setText(title)
                button.setMenu(menu)
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                self.toolbar.addWidget(button)

        spacer = QWidget()
        spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.toolbar.addWidget(spacer)
        self.toolbar.addWidget(self.status_label)
        self._add_toolbar_action("−", lambda: self.view.zoom(-1))
        self.toolbar.addWidget(self.zoom_label)
        self._add_toolbar_action("+", lambda: self.view.zoom(1))
        self.toolbar.addWidget(self.page_button)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.top_bar_toggle = QToolButton()
        self.top_bar_toggle.setText("▲" if config["top_bar_visible"] else "▼")
        self.top_bar_toggle.setToolTip(
            "Collapse top bar" if config["top_bar_visible"] else "Expand top bar"
        )
        self.top_bar_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.top_bar_toggle.setFixedHeight(10)
        self.top_bar_toggle.clicked.connect(self.toggle_top_bar)
        layout.addWidget(self.top_bar_toggle)
        layout.addWidget(self.view, 1)
        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.toolbar.setVisible(bool(config["top_bar_visible"]))

        self.view.pageChanged.connect(self.page_button.setText)
        self.view.zoomChanged.connect(self.zoom_label.setText)
        self.view.pathsDropped.connect(self.open_paths)
        self.view.closeRequested.connect(self.close)
        self.view.fullscreenRequested.connect(self.toggle_fullscreen)
        QShortcut(
            QKeySequence.StandardKey.ZoomIn,
            self,
            activated=lambda: self.view.zoom(1),
        )
        QShortcut(
            QKeySequence.StandardKey.ZoomOut,
            self,
            activated=lambda: self.view.zoom(-1),
        )
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_folder)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Escape"), self, activated=self.close)

    def _add_toolbar_action(self, text: str, callback: object) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        self.toolbar.addAction(action)
        return action

    def _add_menu_action(
        self,
        menu: QMenu,
        text: str,
        callback: object,
    ) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def _add_option_action(self, menu: QMenu, text: str, key: str) -> QAction:
        action = QAction(text, self)
        action.setCheckable(True)
        action.setChecked(bool(self.config[key]))
        action.toggled.connect(self._options_changed)
        menu.addAction(action)
        self._option_actions[key] = action
        return action

    def open_paths(self, paths: Sequence[Path]) -> bool:
        if not paths:
            return False
        resolved = [path.expanduser().resolve() for path in paths]
        new_pdf: PdfBookshelf | None = None
        try:
            if len(resolved) == 1 and resolved[0].is_dir():
                folder = resolved[0]
                pages = scan_bookshelf(folder)
                is_folder = True
            elif (
                len(resolved) == 1
                and resolved[0].is_file()
                and resolved[0].suffix.casefold() in PDF_SUFFIXES
            ):
                folder = resolved[0]
                new_pdf = PdfBookshelf(folder)
                pages = new_pdf.pages
                is_folder = False
            else:
                image_paths = [
                    path
                    for path in resolved
                    if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
                ]
                pages = load_pages(image_paths)
                folder = image_paths[0].parent if image_paths else Path.cwd()
                is_folder = False
        except Exception as error:
            if new_pdf is not None:
                new_pdf.close()
            QMessageBox.critical(self, "Unable to open", str(error))
            return False

        if not pages:
            if new_pdf is not None:
                new_pdf.close()
            message = (
                f"No readable supported images were found in:\n{folder}"
                if len(resolved) == 1
                else "No readable supported images were found."
            )
            QMessageBox.warning(self, "No readable images", message)
            return False

        previous_zoom = (
            self.view.reading_zoom_level
            if self.view.pages
            else str(self.config["zoom_level"])
        )
        self._save_progress()
        if self._active_pdf is not None:
            # A superseded decode may still be reading its temporary PDF page.
            # Keep the provider alive until the application's worker has stopped.
            self._retired_pdfs.append(self._active_pdf)
        self._active_pdf = new_pdf
        self.view.set_pages(pages, folder, is_folder=is_folder)
        self.view.restore_zoom_level(previous_zoom)
        self._update_source_labels()
        QTimer.singleShot(50, self._cleanup_retired_pdfs_when_idle)

        if self.remember_folder and is_folder:
            progress = progress_for_folder(folder)
            if progress is not None:
                answer = QMessageBox.question(
                    self,
                    "Continue reading?",
                    f"Continue reading {folder.name} from page {progress.last_page}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    QTimer.singleShot(
                        0,
                        lambda: self.view.go_to_page_number(progress.last_page),
                    )
        return True

    def _cleanup_retired_pdfs_when_idle(self) -> None:
        if not self._retired_pdfs:
            return
        if self.view._thread_pool.activeThreadCount() > 0:
            QTimer.singleShot(50, self._cleanup_retired_pdfs_when_idle)
            return
        for pdf in self._retired_pdfs:
            pdf.close()
        self._retired_pdfs.clear()

    def _update_source_labels(self) -> None:
        folder = self.view.folder
        count = len(self.view.pages)
        base = folder.name or str(folder)
        if self.view.is_folder:
            self.setWindowTitle(f"Comic and Scroll Reader — {base}")
            self.status_label.setText(base)
            return
        if folder.suffix.casefold() in PDF_SUFFIXES:
            count_label = "1 page" if count == 1 else f"{count} pages"
        else:
            count_label = "1 image" if count == 1 else f"{count} images"
        self.setWindowTitle(
            f"Comic and Scroll Reader — {base} ({count_label})"
        )
        self.status_label.setText(f"{base} ({count_label})")

    def open_folder(self) -> None:
        start = self.view.folder if self.view.folder.is_dir() else self.view.folder.parent
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder containing comic pages",
            str(start),
        )
        if selected:
            self.open_paths([Path(selected)])

    def open_files(self) -> None:
        start = self.view.folder if self.view.folder.is_dir() else self.view.folder.parent
        extensions = " ".join(
            f"*{suffix}" for suffix in sorted(IMAGE_SUFFIXES | PDF_SUFFIXES)
        )
        selected, _filter = QFileDialog.getOpenFileNames(
            self,
            "Choose comic images or a PDF",
            str(start),
            f"Images and PDF ({extensions});;All files (*)",
        )
        if selected:
            self.open_paths([Path(path) for path in selected])

    def _options_changed(self, _checked: bool = False) -> None:
        remember_folder = self._option_actions["remember_folder"].isChecked()
        remember_changed = remember_folder != self.remember_folder
        self.remember_folder = remember_folder
        self.view.set_options(
            dual_page=self._option_actions["dual_page"].isChecked(),
            manga_reading=self._option_actions["manga_reading"].isChecked(),
            page_spacing=self._option_actions["page_spacing"].isChecked(),
            detect_double_spreads=self._option_actions[
                "detect_double_spreads"
            ].isChecked(),
            prevent_image_upscale=self._option_actions[
                "prevent_image_upscale"
            ].isChecked(),
            stop_at_fit_width=self._option_actions[
                "stop_at_fit_width"
            ].isChecked(),
        )
        if remember_changed:
            automatic = load_config()
            automatic["remember_folder"] = self.remember_folder
            try:
                save_config(automatic)
            except OSError as error:
                QMessageBox.critical(
                    self,
                    "Unable to save folder memory",
                    str(error),
                )

    def ask_for_page_number(self) -> None:
        if not self.view.pages:
            return
        page_number, accepted = QInputDialog.getInt(
            self,
            "Go to page",
            f"Enter a page number (1-{len(self.view.pages)}):",
            max(1, self.view.current_page_number),
            1,
            len(self.view.pages),
        )
        if accepted:
            self.view.go_to_page_number(page_number)

    def save_preferences(self) -> None:
        for key, action in self._option_actions.items():
            self.config[key] = action.isChecked()
        self.config["zoom_level"] = self.view.reading_zoom_level
        self.config["top_bar_visible"] = self.toolbar.isVisible()
        self._store_window_state(self.config)
        try:
            save_config(self.config)
        except OSError as error:
            QMessageBox.critical(self, "Unable to save configurations", str(error))
            return
        QMessageBox.information(
            self,
            "Save Configs",
            f"Configurations saved to:\n{CONFIG_PATH}",
        )

    def toggle_top_bar(self) -> None:
        visible = not self.toolbar.isVisible()
        self.toolbar.setVisible(visible)
        self.top_bar_toggle.setText("▲" if visible else "▼")
        self.top_bar_toggle.setToolTip(
            "Collapse top bar" if visible else "Expand top bar"
        )
        automatic = load_config()
        automatic["top_bar_visible"] = visible
        try:
            save_config(automatic)
        except OSError as error:
            QMessageBox.critical(self, "Unable to save top-bar state", str(error))

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            if self._fullscreen_was_maximized:
                self.showMaximized()
            else:
                self.showNormal()
            return
        self._fullscreen_was_maximized = self.isMaximized()
        self.showFullScreen()

    def restore_window_state(self, *, start_maximized: bool | None) -> None:
        geometry_text = self.config.get("window_geometry", "")
        restored_geometry = False
        if isinstance(geometry_text, str):
            match = re.fullmatch(
                r"(\d+)x(\d+)([+-]\d+)([+-]\d+)",
                geometry_text,
            )
            if match is not None:
                width, height, x, y = (int(value) for value in match.groups())
                if width > 0 and height > 0:
                    self.setGeometry(x, y, width, height)
                    restored_geometry = True
        if not restored_geometry:
            screen = QApplication.primaryScreen().availableGeometry()
            self.resize(round(screen.width() * 0.75), round(screen.height() * 0.80))
        should_maximize = (
            bool(self.config["window_maximized"])
            if start_maximized is None
            else start_maximized
        )
        if should_maximize:
            self.showMaximized()
        else:
            self.show()

    def _store_window_state(self, config: dict[str, object]) -> None:
        config["window_maximized"] = (
            self._fullscreen_was_maximized if self.isFullScreen() else self.isMaximized()
        )
        geometry = self.normalGeometry()
        config["window_geometry"] = (
            f"{geometry.width()}x{geometry.height()}"
            f"{geometry.x():+d}{geometry.y():+d}"
        )

    def _save_automatic_state(self) -> None:
        automatic = load_config()
        automatic["remember_folder"] = self.remember_folder
        if self.view.pages:
            automatic["zoom_level"] = self.view.reading_zoom_level
        automatic["top_bar_visible"] = self.toolbar.isVisible()
        self._store_window_state(automatic)
        try:
            save_config(automatic)
        except OSError as error:
            print(f"Warning: unable to save reader state: {error}", file=sys.stderr)

    def _save_progress(self) -> None:
        if not self.remember_folder or not self.view.pages or not self.view.is_folder:
            return
        try:
            save_reading_progress(
                ReadingProgress(
                    folder=self.view.folder,
                    last_page=max(1, self.view.current_page_number),
                )
            )
        except OSError as error:
            print(f"Warning: unable to save reading progress: {error}", file=sys.stderr)

    def closeEvent(self, event: object) -> None:
        self._save_automatic_state()
        self._save_progress()
        self.view.shutdown()
        if self._active_pdf is not None:
            self._active_pdf.close()
            self._active_pdf = None
        for pdf in self._retired_pdfs:
            pdf.close()
        self._retired_pdfs.clear()
        event.accept()


def read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Display a folder of images as a Qt continuous reader."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="folder, PDF, or image files; omit to use the folder picker",
    )
    parser.add_argument(
        "--windowed",
        "--no-maximize",
        "--not-maximized",
        action="store_false",
        dest="start_maximized",
        default=None,
    )
    parser.add_argument("--dual-page", action="store_true")
    parser.add_argument(
        "--manga",
        "--manga-reading",
        action="store_true",
        dest="manga_reading",
    )
    parser.add_argument("--expand-all", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = read_arguments(argv)
    application = QApplication(sys.argv[:1])
    config = load_config()
    if args.dual_page:
        config["dual_page"] = True
    if args.manga_reading:
        config["manga_reading"] = True
    window = QtReaderWindow(config, expand_all=args.expand_all)
    window.restore_window_state(start_maximized=args.start_maximized)
    application.processEvents()

    opened = window.open_paths(args.paths) if args.paths else False
    if not opened:
        window.open_folder()
        opened = bool(window.view.pages)
    if not opened:
        window.close()
        return 0

    window.view.setFocus()
    return application.exec()
