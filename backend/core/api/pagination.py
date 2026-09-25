"""Cursor pagination is the default: stable under concurrent inserts (new inbox
messages do not shift pages) and O(1) per page on large tables, unlike
OFFSET pagination which degrades linearly."""

from rest_framework.pagination import CursorPagination


class StandardCursorPagination(CursorPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200
    ordering: str | tuple[str, ...] = "-created_at"
    cursor_query_param = "cursor"
