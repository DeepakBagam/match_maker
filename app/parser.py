from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

from dateutil import parser as dt_parser

from .schemas import ParsedMessage


_CONTROL_PREFIX_RE = re.compile(r"^[\u200e\u200f\u202a-\u202e\ufeff]+")
_SOURCE_MARKER_RE = re.compile(
    r"^\s*(?:[=\-*#>]+\s*)?(?:source|group|chat)\s*:\s*(?P<source>.+?)\s*(?:[=\-*#>]+\s*)?$",
    re.IGNORECASE,
)
_MESSAGE_START_PATTERNS = [
    re.compile(
        r"^\[(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}),\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?)\]\s*(?P<sender>[^:]+):\s?(?P<body>.*)$"
    ),
    re.compile(
        r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?)\s+-\s+(?P<sender>[^:]+):\s?(?P<body>.*)$"
    ),
]


def _sanitize_line(line: str) -> str:
    line = line.replace("\u202f", " ").replace("\xa0", " ")
    return _CONTROL_PREFIX_RE.sub("", line).strip()


def _match_message_start(line: str):
    cleaned = _sanitize_line(line)
    for pattern in _MESSAGE_START_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return match
    return None


def _parse_timestamp(date_text: str, time_text: str) -> datetime:
    return dt_parser.parse(f"{date_text} {time_text}", dayfirst=True)


def _source_marker(line: str) -> str:
    match = _SOURCE_MARKER_RE.match(_sanitize_line(line))
    if not match:
        return ""
    return match.group("source").strip()


def parse_whatsapp_export(text: str, source: str) -> list[ParsedMessage]:
    lines = text.splitlines()
    entries: list[ParsedMessage] = []
    current: ParsedMessage | None = None

    for line in lines:
        match = _match_message_start(line)
        if match:
            if current is not None:
                entries.append(current)
            timestamp = _parse_timestamp(match.group("date"), match.group("time"))
            current = ParsedMessage(
                timestamp=timestamp,
                sender=match.group("sender").strip(),
                message=match.group("body").strip(),
                source=source,
                raw_message=match.group("body").strip(),
            )
            continue

        if current is not None:
            current.message = f"{current.message}\n{line}".strip()
            current.raw_message = current.message

    if current is not None:
        entries.append(current)

    return entries


def parse_combined_whatsapp_export(text: str, default_source: str) -> list[ParsedMessage]:
    active_source = default_source
    batch_lines: list[str] = []
    entries: list[ParsedMessage] = []
    saw_marker = False

    def flush() -> None:
        nonlocal batch_lines, entries
        if not any(line.strip() for line in batch_lines):
            batch_lines = []
            return
        entries.extend(parse_whatsapp_export("\n".join(batch_lines), active_source))
        batch_lines = []

    for line in text.splitlines():
        source = _source_marker(line)
        if source:
            saw_marker = True
            flush()
            active_source = source
            continue
        batch_lines.append(line)

    flush()
    if entries or saw_marker:
        return entries
    return parse_whatsapp_export(text, default_source)


def filter_recent(messages: Iterable[ParsedMessage], lookback_days: int, now: datetime) -> list[ParsedMessage]:
    if lookback_days <= 0:
        return list(messages)
    threshold = now - timedelta(days=lookback_days)
    return [msg for msg in messages if msg.timestamp >= threshold]
