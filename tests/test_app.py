import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from comic_scroll_reader.app import _save_top_bar_visibility, read_arguments


class ApplicationArgumentsTests(unittest.TestCase):
    def test_reader_starts_maximized_by_default(self) -> None:
        arguments = read_arguments(["pages"])

        self.assertTrue(arguments.start_maximized)
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


if __name__ == "__main__":
    unittest.main()
