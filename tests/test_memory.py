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

    def test_oversized_entry_is_not_cached(self) -> None:
        shelf = MemoryShelf[str, bytes](2, lambda _key, value: len(value))

        shelf.store("large", b"12345")

        self.assertIsNone(shelf.get("large"))
        self.assertEqual(shelf.bytes_used, 0)

    def test_pop_removes_entry_and_decrements_bytes_used(self) -> None:
        shelf = MemoryShelf[str, bytes](10, lambda _key, value: len(value))
        shelf.store("k1", b"123")
        shelf.store("k2", b"4567")
        self.assertEqual(shelf.bytes_used, 7)

        val = shelf.pop("k1")
        self.assertEqual(val, b"123")
        self.assertEqual(shelf.bytes_used, 4)
        self.assertIsNone(shelf.get("k1"))
        self.assertEqual(shelf.get("k2"), b"4567")
        self.assertIsNone(shelf.pop("nonexistent"))


if __name__ == "__main__":
    unittest.main()
