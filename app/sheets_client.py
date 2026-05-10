from __future__ import annotations

from .db_client import (
    DEFAULT_CONFIG,
    DEFAULT_WEIGHTS,
    PROCESSED_MESSAGE_COLUMNS,
    REQUIRED_TABS,
    DatabaseClient,
)

# Backward-compatible alias for older imports inside tests or local scripts.
SheetsClient = DatabaseClient
