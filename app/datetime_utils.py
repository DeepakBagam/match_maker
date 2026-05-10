from __future__ import annotations

from datetime import datetime

from dateutil import parser as dt_parser


def parse_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Missing datetime value")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return dt_parser.parse(text, dayfirst=True)
