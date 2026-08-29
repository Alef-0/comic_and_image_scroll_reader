import unittest
from pathlib import Path

from comic_scroll_reader.app import read_arguments


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
        arguments = read_arguments(["--dual-page", "--manga", "pages"])

        self.assertTrue(arguments.dual_page)
        self.assertTrue(arguments.manga_reading)


if __name__ == "__main__":
    unittest.main()
