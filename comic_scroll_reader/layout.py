"""Pure geometry helpers for the continuous page strip."""

from bisect import bisect_right
from collections.abc import Sequence

from .models import ComicPage, PagePosition


def arrange_pages(
    pages: Sequence[ComicPage], page_width: int, viewport_width: int
) -> list[PagePosition]:
    """Stack scaled pages vertically and center them in the viewport."""
    cursor_y = 0
    x = (viewport_width - page_width) // 2
    positions: list[PagePosition] = []
    for page in pages:
        scaled_height = max(
            1, round(page.native_height * page_width / page.native_width)
        )
        positions.append(PagePosition(x, cursor_y, page_width, scaled_height))
        cursor_y += scaled_height
    return positions


def visible_page_range(
    positions: Sequence[PagePosition], scroll_y: int, viewport_height: int
) -> tuple[int, int]:
    """Return the half-open range of pages visible in the viewport."""
    first = bisect_right(positions, scroll_y, key=lambda position: position.bottom)
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
