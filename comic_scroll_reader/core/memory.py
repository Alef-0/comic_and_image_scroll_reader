"""Memory-aware cache for decoded and canvas-ready images."""

from collections import OrderedDict
from collections.abc import Callable
import ctypes
import sys
from typing import Generic, TypeVar


try:
    if sys.platform.startswith("linux"):
        _libc = ctypes.CDLL("libc.so.6")
        _malloc_trim = _libc.malloc_trim
        _malloc_trim.argtypes = [ctypes.c_size_t]
        _malloc_trim.restype = ctypes.c_int
    else:
        _malloc_trim = None
except Exception:
    _malloc_trim = None


def trim_memory() -> None:
    """Release unused heap memory back to the operating system on Linux."""
    if _malloc_trim is not None:
        try:
            _malloc_trim(0)
        except Exception:
            pass


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
        if size > self.byte_budget:
            return
        self._entries[key] = (value, size)
        self.bytes_used += size

        while self.bytes_used > self.byte_budget:
            _, (_, discarded_size) = self._entries.popitem(last=False)
            self.bytes_used -= discarded_size

    def pop(self, key: Key) -> Value | None:
        found = self._entries.pop(key, None)
        if found is None:
            return None
        self.bytes_used -= found[1]
        return found[0]

    def clear(self) -> None:
        self._entries.clear()
        self.bytes_used = 0

    def keys(self) -> tuple[Key, ...]:
        """Return a stable snapshot of the cached keys."""
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
