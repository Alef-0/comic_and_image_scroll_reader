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
    bottoms = [position.bottom for position in positions]
    first = bisect_right(bottoms, scroll_y)
    viewport_bottom = scroll_y + viewport_height
    last = first
    while last < len(positions) and positions[last].y < viewport_bottom:
        last += 1
    return first, last


def clamp_scroll(scroll_y: int, content_height: int, viewport_height: int) -> int:
    maximum = max(0, content_height - viewport_height)
    return min(max(scroll_y, 0), maximum)

