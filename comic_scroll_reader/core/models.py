"""Small data objects shared by the reader modules."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComicPage:
    file: Path
    native_width: int
    native_height: int


@dataclass(frozen=True, slots=True)
class PagePosition:
    x: int
    y: int
    width: int
    height: int

    @property
    def bottom(self) -> int:
        return self.y + self.height

