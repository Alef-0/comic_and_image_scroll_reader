import unittest

from comic_scroll_reader.ui.window import (
    TOP_BAR_KEY,
    TOP_BAR_TOGGLE_KEY,
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
    def test_group_starts_collapsed_and_toggles_open_and_closed(self) -> None:
        content = FakeElement({"expanded": False})
        header = FakeHeader()
        frame = FakeElement({"header_widget": header})
        window = FakeWindow(
            {
                "-FILE-GROUP-CONTENT-": content,
                "-FILE-GROUP-": frame,
            }
        )

        self.assertTrue(toggle_collapsible_group(window, "-FILE-GROUP-::toggle"))
        self.assertTrue(content.metadata["expanded"])
        self.assertEqual(content.updates[-1], {"visible": True})
        self.assertEqual(header.updates[-1], {"text": "File ▾"})

        toggle_collapsible_group(window, "-FILE-GROUP-::toggle")
        self.assertFalse(content.metadata["expanded"])
        self.assertEqual(content.updates[-1], {"visible": False})
        self.assertEqual(header.updates[-1], {"text": "File ▸"})

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
