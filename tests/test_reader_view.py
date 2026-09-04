import unittest
from unittest.mock import patch
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
        self._next_id = 1

    def after(self, _delay: int, callback: object) -> str:
        job = f"job-{len(self.jobs)}"
        self.jobs.append((job, callback))
        return job

    def after_cancel(self, job: str) -> None:
        self.cancelled.append(job)

    def create_rectangle(self, *args: object, **kwargs: object) -> int:
        item = self._next_id
        self._next_id += 1
        return item

    def create_image(self, *args: object, **kwargs: object) -> int:
        item = self._next_id
        self._next_id += 1
        return item

    def coords(self, *args: object) -> None:
        pass

    def delete(self, *args: object) -> None:
        pass

    def tag_lower(self, *args: object) -> None:
        pass

    def update_idletasks(self) -> None:
        pass


class ReaderViewTests(unittest.TestCase):
    def test_status_omits_detected_spread_count(self) -> None:
        status_updates: list[str] = []
        zoom_updates: list[str] = []
        reader = ComicStrip.__new__(ComicStrip)
        reader.original_size = False
        reader.strip_width = 500
        reader.viewport_width = 1_000
        reader.folder = Path("/pictures/Chapter 1")
        reader.detect_double_spreads = True
        reader.double_spread_indices = {2, 5}
        reader.window = {
            "-STATUS-": SimpleNamespace(update=status_updates.append),
            "-ZOOM-STATUS-": SimpleNamespace(update=zoom_updates.append),
        }

        reader._show_status()

        self.assertEqual(status_updates, ["Chapter 1"])
        self.assertEqual(zoom_updates, ["50%"])

    def test_status_includes_image_count_when_not_folder(self) -> None:
        status_updates: list[str] = []
        zoom_updates: list[str] = []
        reader = ComicStrip.__new__(ComicStrip)
        reader.original_size = False
        reader.strip_width = 500
        reader.viewport_width = 1_000
        reader.folder = Path("/pictures/Chapter 1")
        reader.pages = [object(), object(), object()]
        reader.is_folder = False
        reader.window = {
            "-STATUS-": SimpleNamespace(update=status_updates.append),
            "-ZOOM-STATUS-": SimpleNamespace(update=zoom_updates.append),
        }

        reader._show_status()

        self.assertEqual(status_updates, ["Chapter 1 (3 images)"])
        self.assertEqual(zoom_updates, ["50%"])

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

    def test_saved_zoom_uses_percentage_or_original_mode(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.original_size = False
        reader.strip_width = 450
        reader.viewport_width = 600

        self.assertEqual(reader.reading_zoom_level, "75")

        reader.original_size = True
        self.assertEqual(reader.reading_zoom_level, "original")

    def test_restoring_progress_applies_zoom_before_page(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 800
        reader.viewport_height = 600
        reader.desktop_width = 1_000
        reader.stop_zooming = lambda: None
        reader._maximum_strip_width = lambda: 1_200
        actions: list[tuple[str, int]] = []
        reader._set_strip_width = lambda width, _anchor: actions.append(("zoom", width))
        reader.go_to_page = lambda index: actions.append(("page", index))

        reader.restore_reading_position(6, "75")

        self.assertEqual(actions, [("zoom", 600), ("page", 5)])

    def test_navigation_keys_move_to_ends_and_by_one_screen(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_height = 300
        reader.positions = [PagePosition(0, 0, 100, 1_000)]
        distances: list[int] = []
        zoom_steps: list[int] = []
        reader.scroll = distances.append
        reader.zoom = zoom_steps.append
        reader.toggle_fullscreen = lambda: None
        reader.request_close = lambda: None

        shortcuts = reader._shortcut_actions()
        shortcuts["<Home>"]()
        shortcuts["<End>"]()
        shortcuts["<Prior>"]()
        shortcuts["<Next>"]()
        shortcuts["<plus>"]()
        shortcuts["<minus>"]()

        self.assertEqual(distances, [-700, 700, -250, 250])
        self.assertEqual(zoom_steps, [1, -1])

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
        reader._zoom_cleanup_job = "cleanup"
        reader._memory_trim_job = None
        reader._render_job = "render"
        reader._preload_job = "preload"
        reader._zoom_stash = deque([(1, 100), (1, 100)])
        reader._pending_render_indices = [1, 2]
        reader._pending_preload_indices = [3, 4]

        reader.stop_zooming()

        self.assertEqual(reader.canvas.cancelled, ["zoom", "cleanup", "render", "preload"])
        self.assertEqual(list(reader._zoom_stash), [])
        self.assertEqual(reader._pending_render_indices, [])
        self.assertEqual(reader._pending_preload_indices, [])

    def test_paint_evicts_stale_raw_photos_outside_viewport(self) -> None:
        from comic_scroll_reader.core.memory import MemoryShelf

        reader = ComicStrip.__new__(ComicStrip)
        reader.IMMEDIATE_RENDER_COUNT = 2
        reader.RENDER_STEP_DELAY_MS = 1
        reader.PRELOAD_DISTANCE = 2
        reader.PRELOAD_DELAY_MS = 150
        reader.REGION_GRANULARITY = ComicStrip.REGION_GRANULARITY
        reader.REGION_OVERSCAN = ComicStrip.REGION_OVERSCAN
        reader.viewport_height = 200
        reader.viewport_width = 100
        reader.scroll_y = 400
        reader.pan_x = 0
        reader.canvas = FakeCanvas()
        reader.canvas.coords = lambda *args: None
        reader.canvas.delete = lambda *args: None
        reader.canvas.tag_lower = lambda *args: None
        reader.canvas.update_idletasks = lambda: None
        reader.canvas_pages = {}
        reader._cancel_pending_work = lambda: None
        reader._sync_scrollbar = lambda: None
        reader._show_page_counter = lambda: None
        reader._render_page = lambda idx: None

        # 10 pages, each 100px tall
        reader.pages = [ComicPage(Path(f"p{i}.png"), 100, 100) for i in range(10)]
        reader.positions = [PagePosition(0, i * 100, 100, 100) for i in range(10)]

        # Ready photos with keys for pages 0, 2, 4, 5, 8, 9
        reader.ready_photos = MemoryShelf[tuple[Path, int, int, int, int, int, int], object](
            100, lambda _k, _v: 1
        )
        for i in [0, 2, 4, 5, 8, 9]:
            reader.ready_photos.store(
                (reader.pages[i].file, 100, 100, 0, 0, 100, 100), f"photo_{i}"
            )

        # scroll_y=400 and height=200 covers pages 4 and 5.
        # Raw expansion is strictly viewport-only
        reader.paint()

        # Pages 0, 2, 8, 9 are outside viewport [4, 6) and must be evicted
        for index in (0, 2, 8, 9):
            key = (reader.pages[index].file, 100, 100, 0, 0, 100, 100)
            self.assertIsNone(reader.ready_photos.get(key))

        # Pages 4 and 5 are within viewport [4, 6) and must be retained
        self.assertEqual(
            reader.ready_photos.get((reader.pages[4].file, 100, 100, 0, 0, 100, 100)), "photo_4"
        )
        self.assertEqual(
            reader.ready_photos.get((reader.pages[5].file, 100, 100, 0, 0, 100, 100)), "photo_5"
        )

    def test_zoomed_region_is_bounded_to_viewport_plus_overscan(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.viewport_width = 960
        reader.viewport_height = 1_006
        reader.pan_x = 0
        reader.scroll_y = 5_000
        reader.REGION_GRANULARITY = ComicStrip.REGION_GRANULARITY
        reader.REGION_OVERSCAN = ComicStrip.REGION_OVERSCAN
        page = ComicPage(Path("page.webp"), 4_970, 6_992)
        position = PagePosition(-1_440, 0, 3_840, 5_402)

        key = reader._region_key(page, position)

        self.assertIsNotNone(key)
        maximum_extra = 2 * reader.REGION_OVERSCAN + reader.REGION_GRANULARITY - 1
        self.assertLessEqual(key[5], 960 + maximum_extra)
        self.assertLessEqual(key[6], 1_006 + maximum_extra)
        self.assertLess(key[5] * key[6] * 4, 7 * 1024 * 1024)

    def test_paint_dispatches_cold_pages_to_async_renderer(self) -> None:
        from comic_scroll_reader.core.memory import MemoryShelf
        from comic_scroll_reader.imaging.async_renderer import RenderResult

        reader = ComicStrip.__new__(ComicStrip)
        reader.canvas = FakeCanvas()
        reader.canvas.coords = lambda *args: None
        reader.canvas.delete = lambda *args: None
        reader.canvas.tag_lower = lambda *args: None
        reader.canvas.update_idletasks = lambda: None
        reader._sync_scrollbar = lambda: None
        reader._show_page_counter = lambda: None
        reader.canvas_pages = {}
        reader.viewport_height = 200
        reader.viewport_width = 100
        reader.scroll_y = 0
        reader.pan_x = 0
        reader.REGION_GRANULARITY = 128
        reader.REGION_OVERSCAN = 128
        reader.PRELOAD_DISTANCE = 1
        reader.drawn_webp_cache = MemoryShelf(100, lambda _k, _v: 1)
        reader.ready_photos = MemoryShelf(100, lambda _k, _v: 1)
        reader.pages = [ComicPage(Path(f"p{i}.png"), 100, 100) for i in range(4)]
        reader.positions = [PagePosition(0, i * 100, 100, 100) for i in range(4)]
        reader._render_generation = 1
        reader._async_poll_job = None

        submitted_jobs: list[dict] = []
        mock_renderer = SimpleNamespace(
            submit=lambda **kwargs: submitted_jobs.append(kwargs) or True,
            has_pending_work=True,
            get_results=lambda: [],
            cancel_all=lambda: None,
        )
        reader.async_renderer = mock_renderer

        # paint covers pages 0 and 1
        reader.paint()

        # Pages 1 and 0 are cold and must be submitted to async_renderer (page 1 is at anchor y=100)
        self.assertEqual(len(submitted_jobs), 3)  # 2 visible + 1 preload neighbor
        self.assertEqual(submitted_jobs[0]["page_file"], Path("p1.png"))
        self.assertEqual(submitted_jobs[0]["priority"], 0)
        self.assertEqual(submitted_jobs[1]["page_file"], Path("p0.png"))
        self.assertEqual(submitted_jobs[1]["priority"], 1)
        # Preload for neighbor page 2
        self.assertEqual(submitted_jobs[2]["page_file"], Path("p2.png"))
        self.assertEqual(submitted_jobs[2]["priority"], 10)

        # Async poll job should be scheduled on the canvas
        self.assertIsNotNone(reader._async_poll_job)

    def test_two_phase_zoom_settled_dispatch(self) -> None:
        reader = ComicStrip.__new__(ComicStrip)
        reader.canvas = FakeCanvas()
        reader.strip_width = 100
        reader.desktop_width = 1000
        reader.MIN_WIDTH_RATIO = 0.1
        reader.MAX_WIDTH_RATIO = 4.0
        reader.ZOOM_FACTOR = 1.10
        reader.ZOOM_STEP_DELAY_MS = 1
        reader.ZOOM_IDLE_DELAY_MS = 400
        reader.original_size = False
        reader.viewport_height = 400
        reader.positions = [PagePosition(0, 0, 100, 800)]
        reader.scroll_y = 0
        reader.pan_x = 0
        reader.stop_at_fit_width = False
        reader.prevent_image_upscale = False
        reader.dual_page = False
        reader._zoom_job = None
        reader._zoom_cleanup_job = None
        reader._render_job = None
        reader._preload_job = None
        reader._zoom_stash = deque([(1, 200), (1, 200)])
        reader.pages = []
        reader._arrange_strip = lambda: None
        reader._show_status = lambda: None

        paints: list[bool] = []
        reader.paint = lambda priority_y=None, is_interactive=False: paints.append(is_interactive)

        # Step 1: intermediate zoom (stash still has 1 item left) -> is_interactive=True
        reader._apply_next_zoom_step()
        self.assertEqual(paints, [True])

        # Step 2: final zoom (stash becomes empty) -> is_interactive=False (settled dispatch)
        reader._apply_next_zoom_step()
        self.assertEqual(paints, [True, False])

    def test_paint_creates_and_evicts_placeholder_zones(self) -> None:
        from comic_scroll_reader.core.memory import MemoryShelf

        reader = ComicStrip.__new__(ComicStrip)
        created_rects: list[tuple] = []
        deleted_items: list[int] = []

        reader.canvas = FakeCanvas()
        reader.canvas.create_rectangle = lambda *args, **kwargs: created_rects.append(args) or len(created_rects)
        reader.canvas.delete = lambda item: deleted_items.append(item)
        reader._cancel_pending_work = lambda: None
        reader._sync_scrollbar = lambda: None
        reader._show_page_counter = lambda: None
        reader._render_page = lambda idx: None
        reader.canvas_pages = {}
        reader.canvas_zones = {}
        reader.viewport_height = 200
        reader.viewport_width = 100
        reader.scroll_y = 0
        reader.pan_x = 0
        reader.REGION_GRANULARITY = 128
        reader.REGION_OVERSCAN = 128
        reader.PRELOAD_DISTANCE = 1
        reader.ready_photos = MemoryShelf(100, lambda _k, _v: 1)
        reader.drawn_webp_cache = MemoryShelf(100, lambda _k, _v: 1)
        reader.pages = [ComicPage(Path(f"p{i}.png"), 100, 100) for i in range(4)]
        reader.positions = [PagePosition(0, i * 100, 100, 100) for i in range(4)]

        # Viewport covers pages 0 and 1
        reader.paint()
        self.assertEqual(len(reader.canvas_zones), 2)
        self.assertIn(Path("p0.png"), reader.canvas_zones)
        self.assertIn(Path("p1.png"), reader.canvas_zones)

        # Scroll to pages 2 and 3 -> pages 0 and 1 zones must be evicted
        reader.scroll_y = 200
        reader.paint()
        self.assertEqual(len(reader.canvas_zones), 2)
        self.assertIn(Path("p2.png"), reader.canvas_zones)
        self.assertIn(Path("p3.png"), reader.canvas_zones)
        self.assertNotIn(Path("p0.png"), reader.canvas_zones)
        self.assertNotIn(Path("p1.png"), reader.canvas_zones)
        self.assertEqual(len(deleted_items), 2)

    def test_zoom_uses_blurred_preview_blurb_and_completes_on_async_result(self) -> None:
        from comic_scroll_reader.core.memory import MemoryShelf
        from comic_scroll_reader.imaging.async_renderer import RenderResult

        reader = ComicStrip.__new__(ComicStrip)
        reader.canvas = FakeCanvas()
        created_images: list[tuple] = []
        reader.canvas.create_image = lambda *args, **kwargs: created_images.append((args, kwargs)) or len(created_images)
        reader._cancel_pending_work = lambda: None
        reader._sync_scrollbar = lambda: None
        reader._show_page_counter = lambda: None
        reader.viewport_height = 200
        reader.viewport_width = 100
        reader.scroll_y = 0
        reader.pan_x = 0
        reader.REGION_GRANULARITY = 128
        reader.REGION_OVERSCAN = 128
        reader.PRELOAD_DISTANCE = 1
        reader.ready_photos = MemoryShelf(100, lambda _k, _v: 1)
        reader.drawn_webp_cache = MemoryShelf(100, lambda _k, _v: 1)
        reader.canvas_pages = {}
        reader.canvas_zones = {}
        reader.page_thumbnails = {}
        reader._preview_pages = set()
        reader._render_generation = 1

        page_file = Path("zoom_test.png")
        reader.pages = [ComicPage(page_file, 100, 100)]
        reader.positions = [PagePosition(0, 0, 100, 100)]

        # Provide a thumbnail in cache
        thumb = Image.new("RGB", (50, 50), (120, 130, 140))
        reader.page_thumbnails[page_file] = thumb

        # Mock async_renderer
        submitted = []
        results_queue = []
        reader.async_renderer = SimpleNamespace(
            submit=lambda **kwargs: submitted.append(kwargs) or True,
            has_pending_work=False,
            get_results=lambda: results_queue,
            cancel_all=lambda: None,
        )

        with patch(
            "comic_scroll_reader.ui.reader_view.ImageTk.PhotoImage",
            side_effect=lambda *args, **kwargs: SimpleNamespace(width=lambda: 100, height=lambda: 100),
        ):
            # Paint viewport -> Crisp is not in ready_photos or webp_cache, so blurred preview is used
            reader.paint()

            self.assertIn(page_file, reader._preview_pages)
            self.assertIn(page_file, reader.canvas_pages)
            self.assertEqual(len(submitted), 1)

            # When async worker finishes crisp high-res render:
            crisp_img = Image.new("RGB", (100, 100), (255, 0, 0))
            res = RenderResult(
                generation=1,
                page_file=page_file,
                region_key=reader._region_key(reader.pages[0], reader.positions[0]),
                image=crisp_img,
            )
            results_queue.append(res)

            preloaded = []
            reader._schedule_neighbor_preload = lambda: preloaded.append(True)

            reader._process_async_results()

            # Blurred preview flag should be cleared
            self.assertNotIn(page_file, reader._preview_pages)
            # Because all visible pages are finished, neighbor preload was triggered
            self.assertEqual(preloaded, [True])

        thumb.close()


if __name__ == "__main__":
    unittest.main()


