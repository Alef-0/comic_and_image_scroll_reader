import unittest
from collections import deque
from pathlib import Path

from PIL import Image

from comic_scroll_reader.core.models import ComicPage
from comic_scroll_reader.ui.reader_view import ComicStrip, _resize_filter


class FakeCanvas:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.jobs: list[tuple[str, object]] = []

    def after(self, _delay: int, callback: object) -> str:
        job = f"job-{len(self.jobs)}"
        self.jobs.append((job, callback))
        return job

    def after_cancel(self, job: str) -> None:
        self.cancelled.append(job)


class ReaderViewTests(unittest.TestCase):
    def test_selects_lanczos_for_reduction_and_bilinear_for_enlargement(self) -> None:
        self.assertEqual(
            _resize_filter((100, 200), (200, 400)), Image.Resampling.BILINEAR
        )
        self.assertEqual(
            _resize_filter((100, 200), (50, 100)), Image.Resampling.LANCZOS
        )

    def test_zoom_limits_combine_fit_width_and_native_page_width(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_000
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = False
        reader.dual_page = False
        reader.pages = [
            ComicPage(Path("wide.png"), 900, 1_200),
            ComicPage(Path("narrow.png"), 600, 1_200),
        ]

        self.assertEqual(reader._maximum_strip_width(), 700)
        reader.prevent_image_upscale = True
        self.assertEqual(reader._maximum_strip_width(), 600)

    def test_dual_page_fit_width_allows_half_the_viewport_per_page(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_000
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = False
        reader.dual_page = True

        self.assertEqual(reader._maximum_strip_width(), 350)

    def test_original_size_only_reduces_pages_that_exceed_available_width(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.dual_page = True
        reader.pages = [
            ComicPage(Path("cover.png"), 900, 1_200),
            ComicPage(Path("small.png"), 300, 500),
            ComicPage(Path("large.png"), 600, 900),
        ]

        self.assertEqual(
            [reader._original_page_width(index) for index in range(3)],
            [700, 300, 350],
        )

    def test_opposite_zoom_replaces_the_stash(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.canvas = FakeCanvas()
        reader.viewport_height = 800
        reader._zoom_job = None
        reader._render_job = None
        reader._preload_job = None
        reader._zoom_stash = deque()
        reader._pending_render_indices = []
        reader._pending_preload_indices = []

        reader.zoom(1, anchor_y=100)
        reader.zoom(1, anchor_y=200)
        reader.zoom(-1, anchor_y=300)

        self.assertEqual(list(reader._zoom_stash), [(-1, 300)])
        self.assertEqual(reader.canvas.cancelled, ["job-0"])

    def test_stopping_zoom_cancels_every_pending_stage(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.canvas = FakeCanvas()
        reader._zoom_job = "zoom"
        reader._render_job = "render"
        reader._preload_job = "preload"
        reader._zoom_stash = deque([(1, 100), (1, 100)])
        reader._pending_render_indices = [1, 2]
        reader._pending_preload_indices = [3, 4]

        reader.stop_zooming()

        self.assertEqual(reader.canvas.cancelled, ["zoom", "render", "preload"])
        self.assertEqual(list(reader._zoom_stash), [])
        self.assertEqual(reader._pending_render_indices, [])
        self.assertEqual(reader._pending_preload_indices, [])


if __name__ == "__main__":
    unittest.main()
