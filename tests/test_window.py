import os
import unittest
from pathlib import Path
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

from comic_scroll_reader.ui.window import (
    TOP_BAR_KEY,
    TOP_BAR_TOGGLE_KEY,
    ask_for_bookshelf,
    is_maximized,
    reader_window_title,
    size_from_geometry,
    toggle_collapsible_group,
    toggle_top_bar,
)


class FakeElement:
    def __init__(self, metadata=None) -> None:
        self.metadata = metadata or {}
        self.updates: list[dict[str, object]] = []

    def update(self, **changes: object) -> None:
        self.updates.append(changes)

    def set_tooltip(self, text: str) -> None:
        self.tooltip = text


class FakeHeader:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def configure(self, **changes: object) -> None:
        self.updates.append(changes)


class FakeWindow(dict):
    pass


class WindowControlTests(unittest.TestCase):
    @patch("comic_scroll_reader.ui.window.subprocess.run")
    @patch("comic_scroll_reader.ui.window.shutil.which")
    def test_folder_chooser_is_attached_and_modal_to_parent_window(
        self, which, run
    ) -> None:
        which.return_value = "/usr/bin/zenity"
        run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")
        parent = SimpleNamespace(
            update_idletasks=lambda: None,
            winfo_id=lambda: 4242,
        )

        with patch.dict(
            os.environ,
            {
                "DESKTOP_STARTUP_ID": "stale-startup-token",
                "XDG_ACTIVATION_TOKEN": "stale-activation-token",
            },
        ):
            self.assertIsNone(ask_for_bookshelf(Path("/comics"), parent))
        self.assertIn("--attach=4242", run.call_args.args[0])
        self.assertIn("--modal", run.call_args.args[0])
        dialog_environment = run.call_args.kwargs["env"]
        self.assertNotIn("DESKTOP_STARTUP_ID", dialog_environment)
        self.assertNotIn("XDG_ACTIVATION_TOKEN", dialog_environment)

    def test_destroyed_window_is_not_reported_as_maximized(self) -> None:
        destroyed = SimpleNamespace(
            attributes=lambda _name: (_ for _ in ()).throw(tk.TclError()),
            state=lambda: (_ for _ in ()).throw(tk.TclError()),
        )

        self.assertFalse(is_maximized(SimpleNamespace(TKroot=destroyed)))

    def test_saved_geometry_restores_its_window_size(self) -> None:
        self.assertEqual(size_from_geometry("900x700+20-30"), (900, 700))
        self.assertIsNone(size_from_geometry("not geometry"))

    def test_reader_title_identifies_the_active_folder(self) -> None:
        self.assertEqual(
            reader_window_title(Path("/comics/Chapter 12")),
            "Comic and Scroll Reader — Chapter 12",
        )

    def test_reader_title_with_image_count(self) -> None:
        self.assertEqual(
            reader_window_title(Path("/comics/Chapter 12"), 1),
            "Comic and Scroll Reader — Chapter 12 (1 image)",
        )
        self.assertEqual(
            reader_window_title(Path("/comics/Chapter 12"), 5),
            "Comic and Scroll Reader — Chapter 12 (5 images)",
        )

    def test_reader_title_for_pdf(self) -> None:
        self.assertEqual(
            reader_window_title(Path("/comics/Chapter 12.pdf"), 1),
            "Comic and Scroll Reader — Chapter 12.pdf (1 page)",
        )
        self.assertEqual(
            reader_window_title(Path("/comics/Chapter 12.pdf"), 32),
            "Comic and Scroll Reader — Chapter 12.pdf (32 pages)",
        )

    def test_group_starts_collapsed_and_toggles_open_and_closed(self) -> None:
        content = FakeElement({"expanded": False})
        header = FakeHeader()
        frame = FakeElement({"header_widget": header})
        window = FakeWindow(
            {
                "-IMAGE-SIZE-GROUP-CONTENT-": content,
                "-IMAGE-SIZE-GROUP-": frame,
            }
        )

        self.assertTrue(
            toggle_collapsible_group(window, "-IMAGE-SIZE-GROUP-::toggle")
        )
        self.assertTrue(content.metadata["expanded"])
        self.assertEqual(content.updates[-1], {"visible": True})
        self.assertEqual(header.updates[-1], {"text": "Image size ▾"})

        toggle_collapsible_group(window, "-IMAGE-SIZE-GROUP-::toggle")
        self.assertFalse(content.metadata["expanded"])
        self.assertEqual(content.updates[-1], {"visible": False})
        self.assertEqual(header.updates[-1], {"text": "Image size ▸"})

    def test_non_group_event_is_not_consumed(self) -> None:
        self.assertFalse(toggle_collapsible_group(FakeWindow(), "-OPEN-"))

    def test_top_bar_starts_visible_and_can_be_hidden_and_restored(self) -> None:
        top_bar = FakeElement({"visible": True})
        toggle = FakeElement()
        window = FakeWindow({TOP_BAR_KEY: top_bar, TOP_BAR_TOGGLE_KEY: toggle})

        toggle_top_bar(window)
        self.assertFalse(top_bar.metadata["visible"])
        self.assertEqual(top_bar.updates[-1], {"visible": False})
        self.assertEqual(toggle.updates[-1]["text"], "▼")
        self.assertEqual(toggle.tooltip, "Expand top bar")

        toggle_top_bar(window)
        self.assertTrue(top_bar.metadata["visible"])
        self.assertEqual(top_bar.updates[-1], {"visible": True})
        self.assertEqual(toggle.updates[-1]["text"], "▲")
        self.assertEqual(toggle.tooltip, "Collapse top bar")


if __name__ == "__main__":
    unittest.main()
