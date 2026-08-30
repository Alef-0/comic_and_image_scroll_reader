import tempfile
import unittest
from pathlib import Path

from comic_scroll_reader.reading_progress import (
    ReadingProgress,
    progress_for_folder,
    save_reading_progress,
)


class ReadingProgressTests(unittest.TestCase):
    def test_progress_file_creates_and_updates_one_row_per_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            root = Path(temporary_folder)
            csv_path = root / "config" / "reading_progress.csv"
            first = root / "Comic One"
            second = root / "Comic Two"

            save_reading_progress(ReadingProgress(first, 3), csv_path)
            save_reading_progress(ReadingProgress(second, 8), csv_path)
            save_reading_progress(ReadingProgress(first, 5), csv_path)

            self.assertEqual(
                progress_for_folder(first, csv_path),
                ReadingProgress(first.resolve(), 5),
            )
            self.assertEqual(
                progress_for_folder(second, csv_path),
                ReadingProgress(second.resolve(), 8),
            )
            lines = csv_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "folder,last_page")
            self.assertEqual(len(lines), 3)

    def test_invalid_rows_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_folder:
            csv_path = Path(temporary_folder) / "reading_progress.csv"
            csv_path.write_text(
                "folder,last_page,zoom_level\n/comic,not-a-page,75\n",
                encoding="utf-8",
            )

            self.assertIsNone(progress_for_folder(Path("/comic"), csv_path))


if __name__ == "__main__":
    unittest.main()
