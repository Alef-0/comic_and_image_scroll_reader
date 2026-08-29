import unittest

from comic_scroll_reader.core.memory import MemoryShelf


class MemoryShelfTests(unittest.TestCase):
    def test_least_recently_used_entry_is_removed(self) -> None:
        shelf = MemoryShelf[str, bytes](5, lambda _key, value: len(value))
        shelf.store("a", b"aa")
        shelf.store("b", b"bb")
        shelf.get("a")

        shelf.store("c", b"cc")

        self.assertIsNone(shelf.get("b"))
        self.assertEqual(shelf.get("a"), b"aa")
        self.assertEqual(shelf.get("c"), b"cc")

    def test_one_oversized_entry_is_retained(self) -> None:
        shelf = MemoryShelf[str, bytes](2, lambda _key, value: len(value))

        shelf.store("large", b"12345")

        self.assertEqual(shelf.get("large"), b"12345")


if __name__ == "__main__":
    unittest.main()
