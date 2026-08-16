from __future__ import annotations

import enum


class ContentStatus(enum.StrEnum):
    IMPORTANT = "important"
    CURRENT = "current"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
