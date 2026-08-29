"""Memory-aware cache for decoded and canvas-ready images."""

from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar


Key = TypeVar("Key")
Value = TypeVar("Value")


class MemoryShelf(Generic[Key, Value]):
    """A least-recently-used cache measured in bytes instead of item count."""

    def __init__(
        self,
        byte_budget: int,
        measure: Callable[[Key, Value], int],
    ) -> None:
        if byte_budget < 1:
            raise ValueError("byte_budget must be positive")
        self.byte_budget = byte_budget
        self.bytes_used = 0
        self._measure = measure
        self._entries: OrderedDict[Key, tuple[Value, int]] = OrderedDict()

    def get(self, key: Key) -> Value | None:
        found = self._entries.pop(key, None)
        if found is None:
            return None
        self._entries[key] = found
        return found[0]

    def store(self, key: Key, value: Value) -> None:
        replaced = self._entries.pop(key, None)
        if replaced is not None:
            self.bytes_used -= replaced[1]

        size = max(0, self._measure(key, value))
        self._entries[key] = (value, size)
        self.bytes_used += size

        # Keep one oversized item. Decoding it again would be slower and would
        # not lower the peak memory use that was already required to open it.
        while self.bytes_used > self.byte_budget and len(self._entries) > 1:
            _, (_, discarded_size) = self._entries.popitem(last=False)
            self.bytes_used -= discarded_size

    def clear(self) -> None:
        self._entries.clear()
        self.bytes_used = 0

    def __len__(self) -> int:
        return len(self._entries)

