"""Find, inspect, and open comic pages from a directory."""

from pathlib import Path
import re
import sys

from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.models import ComicPage


IMAGE_SUFFIXES = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".jp2", ".png", ".tif", ".tiff", ".webp"}
)


def natural_file_key(file: Path) -> list[tuple[int, object]]:
    """Build a sort key that places page2 before page10."""
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", file.name)
    ]


def display_size(image: Image.Image) -> tuple[int, int]:
    """Return image dimensions after accounting for EXIF orientation."""
    try:
        orientation = image.getexif().get(274, 1)
    except (AttributeError, TypeError, ValueError):
        orientation = 1
    width, height = image.size
    if orientation in {5, 6, 7, 8}:
        return height, width
    return width, height


def open_page(file: Path) -> Image.Image | None:
    """Decode a page, correct its orientation, and detach it from the file."""
    try:
        with Image.open(file) as source:
            return ImageOps.exif_transpose(source).convert("RGB").copy()
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def scan_bookshelf(folder: Path) -> list[ComicPage]:
    """Return image pages with readable metadata in natural filename order."""
    candidates = sorted(
        (
            file
            for file in folder.iterdir()
            if file.is_file() and file.suffix.casefold() in IMAGE_SUFFIXES
        ),
        key=natural_file_key,
    )

    pages: list[ComicPage] = []
    for file in candidates:
        try:
            with Image.open(file) as image:
                width, height = display_size(image)
        except (OSError, ValueError, UnidentifiedImageError):
            print(f"Warning: unable to read {file.name}; skipping it.", file=sys.stderr)
            continue
        if width > 0 and height > 0:
            pages.append(ComicPage(file, width, height))
    return pages
