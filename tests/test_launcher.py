import tempfile
import unittest
from pathlib import Path

from comic_scroll_reader.config import DEFAULT_CONFIG
from comic_scroll_reader.ui.launcher import (
    CONFIG_KEYS,
    config_from_values,
    dropped_folder,
    dropped_images,
    dropped_pdf,
    dropped_target,
)


class FakeInterpreter:
    def splitlist(self, data: str) -> tuple[str, ...]:
        return tuple(data.split("|"))


class FakeRoot:
    tk = FakeInterpreter()


class LauncherTests(unittest.TestCase):
    def test_config_values_use_the_persisted_schema(self) -> None:
        values = {
            element_key: not DEFAULT_CONFIG[name]
            for name, element_key in CONFIG_KEYS.items()
        }

        expected = DEFAULT_CONFIG.copy()
        expected.update(
            {name: not DEFAULT_CONFIG[name] for name in CONFIG_KEYS}
        )
        self.assertEqual(config_from_values(values), expected)

    def test_config_values_preserve_the_hidden_top_bar_setting(self) -> None:
        current = DEFAULT_CONFIG | {"top_bar_visible": False}

        saved = config_from_values({}, current)

        self.assertFalse(saved["top_bar_visible"])

    def test_drop_uses_the_first_directory_and_ignores_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / "page.png"
            file.touch()
            comic = root / "comic"
            comic.mkdir()

            selected = dropped_folder(f"{file}|{comic}", FakeRoot())

        self.assertEqual(selected, comic)

    def test_drop_without_a_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "page.png"
            file.touch()

            self.assertIsNone(dropped_folder(str(file), FakeRoot()))

    def test_dropped_images_returns_existing_image_files_in_natural_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p10 = root / "p10.png"
            p2 = root / "p2.jpg"
            p1 = root / "p1.webp"
            txt = root / "readme.txt"
            p10.touch()
            p2.touch()
            p1.touch()
            txt.touch()

            images = dropped_images(f"{p10}|{p2}|{txt}|{p1}", FakeRoot())

        self.assertEqual(images, [p1, p2, p10])

    def test_dropped_target_prefers_folder_if_present(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            img = root / "cover.jpg"
            img.touch()
            comic = root / "comic_dir"
            comic.mkdir()

            target = dropped_target(f"{img}|{comic}", FakeRoot())

        self.assertEqual(target, comic)

    def test_dropped_target_returns_images_when_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            p2 = root / "02.jpg"
            p1 = root / "01.jpg"
            p2.touch()
            p1.touch()

            target = dropped_target(f"{p2}|{p1}", FakeRoot())

        self.assertEqual(target, [p1, p2])

    def test_dropped_target_returns_none_when_no_valid_folder_or_images(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            txt = Path(folder) / "notes.txt"
            txt.touch()

            self.assertIsNone(dropped_target(str(txt), FakeRoot()))

    def test_dropped_pdf_returns_pdf_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "comic.pdf"
            pdf.touch()

            self.assertEqual(dropped_pdf(str(pdf), FakeRoot()), pdf)

    def test_dropped_target_returns_pdf_when_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pdf = root / "comic.pdf"
            img = root / "cover.png"
            pdf.touch()
            img.touch()

            target = dropped_target(f"{pdf}|{img}", FakeRoot())

        self.assertEqual(target, pdf)


if __name__ == "__main__":
    unittest.main()
