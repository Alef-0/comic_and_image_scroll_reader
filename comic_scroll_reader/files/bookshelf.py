"""Find, inspect, and open comic pages from a directory or file list."""

from collections.abc import Iterable
from pathlib import Path
import re
import sys

from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.models import ComicPage


IMAGE_SUFFIXES = frozenset(
    {".bmp", ".gif", ".jpeg", ".jpg", ".jp2", ".png", ".tif", ".tiff", ".webp"}
)
PDF_SUFFIXES = frozenset({".pdf"})


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
    if not file.is_file():
        from .pdf_reader import ensure_page_available

        ensure_page_available(file)
    try:
        with Image.open(file) as source:
            oriented = ImageOps.exif_transpose(source)
            try:
                image = oriented.convert("RGB")
                image.load()
                return image
            finally:
                if oriented is not source:
                    oriented.close()
    except (OSError, ValueError, UnidentifiedImageError):
        return None


def render_page_region(
    file: Path,
    source_box: tuple[float, float, float, float],
    target_size: tuple[int, int],
    resample: Image.Resampling = Image.Resampling.BICUBIC,
) -> Image.Image | None:
    """Render one source region at display size without retaining the decoded page."""
    from .pdf_reader import ensure_page_available, render_registered_page_region

    rendered = render_registered_page_region(file, source_box, target_size)
    if rendered is not None:
        return rendered
    if not file.is_file():
        ensure_page_available(file)

    width, height = target_size
    if width < 1 or height < 1:
        return None
    try:
        with Image.open(file) as source:
            oriented = ImageOps.exif_transpose(source)
            converted = oriented
            try:
                if oriented.mode != "RGB":
                    converted = oriented.convert("RGB")
                img_w, img_h = float(converted.width), float(converted.height)
                x0 = max(0.0, min(img_w, float(source_box[0])))
                y0 = max(0.0, min(img_h, float(source_box[1])))
                x1 = max(x0, min(img_w, float(source_box[2])))
                y1 = max(y0, min(img_h, float(source_box[3])))
                if x1 <= x0 or y1 <= y0:
                    return None
                rendered = converted.resize(
                    (width, height),
                    resample=resample,
                    box=(x0, y0, x1, y1),
                )
                rendered.load()
                return rendered
            finally:
                if converted is not oriented:
                    converted.close()
                if oriented is not source:
                    oriented.close()
    except (OSError, ValueError, UnidentifiedImageError):
        return None



def load_pages(files: Iterable[Path]) -> list[ComicPage]:
    """Return image pages from a collection of files in natural filename order."""
    unique_files = {file.resolve(): file for file in files if file.is_file()}
    candidates = sorted(
        (
            file
            for file in unique_files.values()
            if file.suffix.casefold() in IMAGE_SUFFIXES
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


def scan_bookshelf(folder: Path) -> list[ComicPage]:
    """Return image pages with readable metadata in natural filename order."""
    return load_pages(folder.iterdir())
