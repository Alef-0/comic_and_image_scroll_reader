"""Pure geometry helpers for the continuous page strip."""

from bisect import bisect_right
from collections.abc import Collection, Sequence
from statistics import median

from .models import ComicPage, PagePosition


def arrange_pages(
    pages: Sequence[ComicPage],
    page_width: int | Sequence[int],
    viewport_width: int,
    *,
    dual_page: bool = False,
    manga_reading: bool = False,
    page_gap: int = 0,
    solo_page_indices: Collection[int] = (),
) -> list[PagePosition]:
    """Arrange pages in a centered strip, keeping requested pages in solo rows."""
    widths = (
        [page_width] * len(pages)
        if isinstance(page_width, int)
        else list(page_width)
    )
    cursor_y = 0
    positions: list[PagePosition] = []
    solo_pages = set(solo_page_indices)
    index = 0
    while index < len(pages):
        row_indices = [index]
        next_index = index + 1
        if (
            dual_page
            and index != 0
            and index not in solo_pages
            and next_index < len(pages)
            and next_index not in solo_pages
        ):
            row_indices.append(next_index)
        display_indices = list(reversed(row_indices)) if manga_reading else row_indices
        row_width = sum(widths[item] for item in row_indices)
        row_width += page_gap * max(0, len(row_indices) - 1)
        cursor_x = (viewport_width - row_width) // 2
        row_height = 0
        row_positions: dict[int, PagePosition] = {}
        for item in display_indices:
            width = widths[item]
            page = pages[item]
            height = max(1, round(page.native_height * width / page.native_width))
            row_positions[item] = PagePosition(cursor_x, cursor_y, width, height)
            cursor_x += width + page_gap
            row_height = max(row_height, height)
        positions.extend(row_positions[item] for item in row_indices)
        cursor_y += row_height
        index += len(row_indices)
        if index < len(pages):
            cursor_y += page_gap
    return positions


def detect_double_spread_indices(
    pages: Sequence[ComicPage], width_ratio_multiplier: float = 1.5
) -> set[int]:
    """Detect pages substantially wider than the folder's typical page shape."""
    if len(pages) < 2:
        return set()
    ratios = [page.native_width / page.native_height for page in pages]
    sorted_ratios = sorted(ratios)
    typical_sample = sorted_ratios[: max(1, (len(sorted_ratios) + 1) // 2)]
    typical_ratio = median(typical_sample)
    threshold = typical_ratio * width_ratio_multiplier
    return {index for index, ratio in enumerate(ratios) if ratio > threshold}


def visible_page_range(
    positions: Sequence[PagePosition], scroll_y: int, viewport_height: int
) -> tuple[int, int]:
    """Return the half-open range of pages visible in the viewport."""
    if not positions:
        return 0, 0

    first = max(0, bisect_right(positions, scroll_y, key=lambda position: position.y) - 1)
    while first > 0 and positions[first - 1].y == positions[first].y:
        first -= 1
    while first < len(positions):
        row_y = positions[first].y
        row_end = first
        row_bottom = 0
        while row_end < len(positions) and positions[row_end].y == row_y:
            row_bottom = max(row_bottom, positions[row_end].bottom)
            row_end += 1
        if row_bottom > scroll_y:
            break
        first = row_end

    viewport_bottom = scroll_y + viewport_height
    last = first
    while last < len(positions) and positions[last].y < viewport_bottom:
        last += 1
    return first, last


def pages_nearest_to(
    positions: Sequence[PagePosition], indices: Sequence[int], target_y: int
) -> list[int]:
    """Order page indices from nearest to farthest from a reading position."""

    def distance(index: int) -> tuple[int, int, int]:
        position = positions[index]
        if position.y <= target_y < position.bottom:
            return 0, 0, index
        nearest_edge = min(abs(target_y - position.y), abs(target_y - position.bottom))
        # When two pages are equally close, prefer the next page in reading
        # order over one the reader has just passed.
        is_previous_page = int(position.bottom <= target_y)
        return nearest_edge, is_previous_page, index

    return sorted(indices, key=distance)


def neighboring_page_indices(
    first: int, last: int, page_count: int, distance: int
) -> list[int]:
    """Return nearby page indices, alternating backward and forward."""
    neighbors: list[int] = []
    for offset in range(1, distance + 1):
        for index in (first - offset, last + offset - 1):
            if 0 <= index < page_count:
                neighbors.append(index)
    return neighbors


def clamp_scroll(scroll_y: int, content_height: int, viewport_height: int) -> int:
    maximum = max(0, content_height - viewport_height)
    return min(max(scroll_y, 0), maximum)
