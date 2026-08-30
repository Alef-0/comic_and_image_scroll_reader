import json
import tempfile
import unittest
from pathlib import Path

from comic_scroll_reader.config import DEFAULT_CONFIG, load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_missing_or_invalid_config_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reader_config.json"
            self.assertEqual(load_config(path), DEFAULT_CONFIG)

            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_config(path), DEFAULT_CONFIG)

    def test_config_round_trip_keeps_every_reader_setting(self) -> None:
        expected = DEFAULT_CONFIG.copy()
        expected.update(
            {
                key: not value
                for key, value in DEFAULT_CONFIG.items()
                if isinstance(value, bool)
            }
        )
        expected["window_geometry"] = "900x700+20+30"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "reader_config.json"
            save_config(expected, path)

            self.assertEqual(load_config(path), expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)

    def test_unknown_and_wrong_typed_values_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reader_config.json"
            path.write_text(
                json.dumps(
                    {
                        "dual_page": True,
                        "page_spacing": 1,
                        "window_geometry": False,
                        "extra": True,
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertTrue(config["dual_page"])
        self.assertEqual(config["page_spacing"], DEFAULT_CONFIG["page_spacing"])
        self.assertEqual(
            config["window_geometry"], DEFAULT_CONFIG["window_geometry"]
        )
        self.assertNotIn("extra", config)


if __name__ == "__main__":
    unittest.main()
