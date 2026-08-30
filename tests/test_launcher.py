import tempfile
import unittest
from pathlib import Path

from comic_scroll_reader.config import DEFAULT_CONFIG
from comic_scroll_reader.ui.launcher import CONFIG_KEYS, config_from_values, dropped_folder


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


if __name__ == "__main__":
    unittest.main()
