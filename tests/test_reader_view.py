import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from comic_scroll_reader.core.models import ComicPage, PagePosition
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
    def test_counter_uses_the_last_visible_page(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.pages = [object(), object(), object()]
        reader.positions = [
            PagePosition(0, 0, 100, 200),
            PagePosition(0, 200, 100, 200),
            PagePosition(0, 400, 100, 50),
        ]
        reader.dual_page = False
        reader._visible_range = (1, 3)

        self.assertEqual(reader.current_page_number, 3)
        self.assertEqual(reader.page_counter_text, "3 / 3")

    def test_dual_page_counter_shows_the_last_visible_spread(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.pages = [object(), object(), object(), object(), object()]
        reader.positions = [
            PagePosition(100, 0, 100, 200),
            PagePosition(100, 200, 100, 200),
            PagePosition(200, 200, 100, 200),
            PagePosition(100, 400, 100, 200),
            PagePosition(200, 400, 100, 200),
        ]
        reader.dual_page = True
        reader._visible_range = (0, 3)

        self.assertEqual(reader.page_counter_text, "2-3 / 5")

    def test_go_to_page_clamps_indices_to_the_available_range(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.positions = [
            PagePosition(0, 0, 100, 100),
            PagePosition(0, 100, 100, 100),
            PagePosition(0, 200, 100, 100),
        ]
        destinations: list[int] = []
        reader.scroll_to = destinations.append

        reader.go_to_page(-10)
        reader.go_to_page(1)
        reader.go_to_page(10)

        self.assertEqual(destinations, [0, 100, 200])

    def test_go_to_page_number_accepts_one_based_input_and_rejects_text(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        destinations: list[int] = []
        reader.go_to_page = destinations.append

        self.assertTrue(reader.go_to_page_number(" 3 "))
        self.assertFalse(reader.go_to_page_number("three"))
        self.assertEqual(destinations, [2])

    def test_navigation_keys_move_to_ends_and_by_one_screen(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_height = 300
        reader.positions = [PagePosition(0, 0, 100, 1_000)]
        distances: list[int] = []
        reader.scroll = distances.append
        reader.zoom = lambda _steps: None
        reader.toggle_fullscreen = lambda: None
        reader.request_close = lambda: None

        shortcuts = reader._shortcut_actions()
        shortcuts["<Home>"]()
        shortcuts["<End>"]()
        shortcuts["<Prior>"]()
        shortcuts["<Next>"]()

        self.assertEqual(distances, [-700, 700, -250, 250])

    def test_numlock_off_numpad_navigation_uses_keypad_symbols(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_height = 300
        reader.positions = [PagePosition(0, 0, 100, 1_000)]
        distances: list[int] = []
        reader.scroll = distances.append
        reader.zoom = lambda _steps: None
        reader.toggle_fullscreen = lambda: None
        reader.request_close = lambda: None

        shortcuts = reader._shortcut_actions()
        shortcuts["<KP_Home>"]()
        shortcuts["<KP_End>"]()
        shortcuts["<KP_Prior>"]()
        shortcuts["<KP_Next>"]()

        self.assertEqual(distances, [-700, 700, -250, 250])
        self.assertNotIn("<KP_7>", shortcuts)
        self.assertNotIn("<KP_1>", shortcuts)
        self.assertNotIn("<KP_9>", shortcuts)
        self.assertNotIn("<KP_3>", shortcuts)

    def test_numlock_off_numpad_arrows_scroll_and_pan(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 700
        reader.viewport_height = 300
        reader.positions = [PagePosition(-250, 0, 1_200, 1_000)]
        reader.pan_x = 0
        distances: list[int] = []
        reader.scroll = distances.append
        reader.zoom = lambda _steps: None
        reader.toggle_fullscreen = lambda: None
        reader.request_close = lambda: None
        reader.stop_zooming = lambda: None
        reader.paint = lambda: None

        shortcuts = reader._shortcut_actions()
        shortcuts["<KP_Up>"]()
        shortcuts["<KP_Down>"]()
        shortcuts["<KP_Left>"]()
        shortcuts["<KP_Right>"]()

        self.assertEqual(distances, [-120, 120])
        self.assertEqual(reader.pan_x, 0)
        self.assertNotIn("<KP_8>", shortcuts)
        self.assertNotIn("<KP_4>", shortcuts)
        self.assertNotIn("<KP_6>", shortcuts)
        self.assertNotIn("<KP_2>", shortcuts)

    def test_selects_bicubic_for_reduction_and_enlargement(self) -> None:
        self.assertEqual(
            _resize_filter((100, 200), (200, 400)), Image.Resampling.BICUBIC
        )
        self.assertEqual(
            _resize_filter((100, 200), (50, 100)), Image.Resampling.BICUBIC
        )

    def test_native_width_limit_is_applied_to_each_page_independently(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_000
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = True
        reader.dual_page = False
        reader.double_spread_indices = set()
        reader.typical_page_ratio = 0.75
        reader.strip_width = 700
        reader.pages = [
            ComicPage(Path("wide.png"), 900, 1_200),
            ComicPage(Path("narrow.png"), 600, 1_200),
        ]

        self.assertEqual(reader._maximum_strip_width(), 700)
        self.assertEqual(reader._scaled_page_widths(), [700, 600])

    def test_one_narrow_page_does_not_limit_the_rest_of_the_folder(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_920
        reader.viewport_width = 1_000
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = True
        reader.dual_page = False
        reader.double_spread_indices = set()
        reader.typical_page_ratio = 0.75
        reader.strip_width = 1_000
        reader.pages = [
            ComicPage(Path("page-1.webp"), 1_280, 1_837),
            ComicPage(Path("small.webp"), 400, 579),
            ComicPage(Path("page-3.webp"), 1_280, 1_837),
        ]

        self.assertEqual(reader._maximum_strip_width(), 1_000)
        self.assertEqual(reader._scaled_page_widths(), [1_000, 400, 1_000])

    def test_dual_page_fit_width_allows_half_the_viewport_per_page(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_000
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = False
        reader.dual_page = True
        reader.page_spacing = False

        self.assertEqual(reader._maximum_strip_width(), 350)

    def test_dual_page_fit_width_reserves_space_for_the_gap(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.desktop_width = 1_000
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.prevent_image_upscale = False
        reader.dual_page = True
        reader.page_spacing = True

        self.assertEqual(reader._maximum_strip_width(), 344)

    def test_original_size_only_reduces_pages_that_exceed_available_width(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 700
        reader.stop_at_fit_width = True
        reader.dual_page = True
        reader.page_spacing = False
        reader.double_spread_indices = set()
        reader.pages = [
            ComicPage(Path("cover.png"), 900, 1_200),
            ComicPage(Path("small.png"), 300, 500),
            ComicPage(Path("large.png"), 600, 900),
        ]

        self.assertEqual(
            [reader._original_page_width(index) for index in range(3)],
            [700, 300, 350],
        )

    def test_spread_width_matches_regular_page_height_until_fit_limit(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.pages = [
            ComicPage(Path("one.png"), 600, 900),
            ComicPage(Path("two.png"), 600, 900),
            ComicPage(Path("spread.png"), 1_200, 900),
        ]
        reader.strip_width = 300
        reader.desktop_width = 1_000
        reader.typical_page_ratio = 2 / 3
        reader.double_spread_indices = {2}
        reader.viewport_width = 500
        reader.page_spacing = True
        reader.prevent_image_upscale = False
        reader.stop_at_fit_width = False

        self.assertEqual(reader._scaled_page_widths(), [300, 300, 600])
        reader.stop_at_fit_width = True
        self.assertEqual(reader._scaled_page_widths(), [300, 300, 500])

    def test_horizontal_pan_is_limited_to_the_extra_image_width(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 700
        reader.positions = [PagePosition(-250, 0, 1_200, 900)]

        self.assertEqual(reader._clamped_pan_x(-500), -250)
        self.assertEqual(reader._clamped_pan_x(500), 250)

    def test_drag_moves_the_canvas_horizontally_and_vertically(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 700
        reader.viewport_height = 500
        reader.positions = [PagePosition(-250, 0, 1_200, 1_200)]
        reader.pan_x = 0
        reader.scroll_y = 300
        reader._drag_last = (100, 100)
        paints: list[bool] = []
        reader.paint = lambda: paints.append(True)

        reader._drag_moved(SimpleNamespace(x=150, y=140))

        self.assertEqual((reader.pan_x, reader.scroll_y), (50, 260))
        self.assertEqual(paints, [True])

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
