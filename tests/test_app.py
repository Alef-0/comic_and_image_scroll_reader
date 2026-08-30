import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from comic_scroll_reader.app import (
    _offer_to_resume,
    _save_current_progress,
    _save_top_bar_visibility,
    _save_window_state,
    read_arguments,
)
from comic_scroll_reader.reading_progress import ReadingProgress


class ApplicationArgumentsTests(unittest.TestCase):
    def test_reader_uses_saved_window_state_by_default(self) -> None:
        arguments = read_arguments(["pages"])

        self.assertIsNone(arguments.start_maximized)
        self.assertEqual(arguments.folder, Path("pages"))

    def test_windowed_flag_disables_startup_maximization(self) -> None:
        arguments = read_arguments(["--windowed", "pages"])

        self.assertFalse(arguments.start_maximized)

    def test_not_maximized_alias_disables_startup_maximization(self) -> None:
        arguments = read_arguments(["--not-maximized", "pages"])

        self.assertFalse(arguments.start_maximized)

    def test_no_maximize_alias_disables_startup_maximization(self) -> None:
        arguments = read_arguments(["--no-maximize", "pages"])

        self.assertFalse(arguments.start_maximized)

    def test_page_layout_flags_enable_startup_options(self) -> None:
        arguments = read_arguments(
            ["--dual-page", "--manga", "--expand-all", "pages"]
        )

        self.assertTrue(arguments.dual_page)
        self.assertTrue(arguments.manga_reading)
        self.assertTrue(arguments.expand_all)

    @patch("comic_scroll_reader.app.save_config")
    @patch("comic_scroll_reader.app.load_config")
    def test_top_bar_visibility_is_saved_without_changing_other_settings(
        self, load_config_mock, save_config_mock
    ) -> None:
        existing = {"dual_page": True, "top_bar_visible": True}
        load_config_mock.return_value = existing.copy()
        reader = SimpleNamespace(
            window={"-TOP-BAR-": SimpleNamespace(metadata={"visible": False})}
        )

        _save_top_bar_visibility(reader)

        save_config_mock.assert_called_once_with(
            {"dual_page": True, "top_bar_visible": False}
        )

    @patch("comic_scroll_reader.app.save_reading_progress")
    def test_closing_progress_contains_only_folder_page_and_zoom(
        self, save_progress_mock
    ) -> None:
        reader = SimpleNamespace(
            folder=Path("/comics/Chapter 1"),
            current_page_number=12,
            reading_zoom_level="90",
        )

        _save_current_progress(reader)

        save_progress_mock.assert_called_once_with(
            ReadingProgress(Path("/comics/Chapter 1"), 12, "90")
        )

    @patch("comic_scroll_reader.app.sg.popup_yes_no", return_value="Yes")
    @patch("comic_scroll_reader.app.progress_for_folder")
    def test_saved_progress_is_restored_after_confirmation(
        self, progress_mock, _popup_mock
    ) -> None:
        progress_mock.return_value = ReadingProgress(
            Path("/comics/Chapter 1"), 12, "90"
        )
        restored: list[tuple[int, str]] = []
        reader = SimpleNamespace(
            folder=Path("/comics/Chapter 1"),
            restore_reading_position=lambda page, zoom: restored.append(
                (page, zoom)
            ),
        )

        _offer_to_resume(reader)

        self.assertEqual(restored, [(12, "90")])

    @patch("comic_scroll_reader.app.save_config")
    @patch("comic_scroll_reader.app.load_config")
    def test_window_state_updates_geometry_and_maximized_status(
        self, load_config_mock, save_config_mock
    ) -> None:
        load_config_mock.return_value = {"dual_page": True}

        _save_window_state(SimpleNamespace(), "900x700+20+30", False)

        save_config_mock.assert_called_once_with(
            {
                "dual_page": True,
                "window_geometry": "900x700+20+30",
                "window_maximized": False,
            }
        )


if __name__ == "__main__":
    unittest.main()
