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
    try:
        day_text, month_text, year_text = re.split(r"[/-]", date_text.strip())
        day = int(day_text)
        month = int(month_text)
        year = int(year_text)
        if year < 100:
            year += 2000

        normalized_time = time_text.strip().lower().replace(" ", "")
        meridiem = ""
        if normalized_time.endswith(("am", "pm")):
            meridiem = normalized_time[-2:]
            normalized_time = normalized_time[:-2]

        time_parts = [int(part) for part in normalized_time.split(":")]
        hour = time_parts[0]
        minute = time_parts[1]
        second = time_parts[2] if len(time_parts) > 2 else 0

        if meridiem:
            if hour == 12:
                hour = 0
            if meridiem == "pm":
                hour += 12

        return datetime(year, month, day, hour, minute, second)
    except Exception:
        return dt_parser.parse(f"{date_text} {time_text}", dayfirst=True)


def _source_marker(line: str) -> str:
    match = _SOURCE_MARKER_RE.match(_sanitize_line(line))
    if not match:
        return ""
    return match.group("source").strip()

def _parse_whatsapp_lines(lines: Iterable[str], default_source: str, *, allow_source_markers: bool) -> list[ParsedMessage]:
    active_source = default_source
    entries: list[ParsedMessage] = []
    current: ParsedMessage | None = None

    for line in lines:
        if allow_source_markers:
            source = _source_marker(line)
            if source:
                if current is not None:
                    entries.append(current)
                    current = None
                active_source = source
                continue

        match = _match_message_start(line)
        if match:
            if current is not None:
                entries.append(current)
            timestamp = _parse_timestamp(match.group("date"), match.group("time"))
            current = ParsedMessage(
                timestamp=timestamp,
                sender=match.group("sender").strip(),
                message=match.group("body").strip(),
                source=active_source,
                raw_message=match.group("body").strip(),
            )
            continue

        if current is not None:
            current.message = f"{current.message}\n{line}".strip()
            current.raw_message = current.message

    if current is not None:
        entries.append(current)

    return entries


def parse_whatsapp_export(text: str, source: str) -> list[ParsedMessage]:
    return _parse_whatsapp_lines(text.splitlines(), source, allow_source_markers=False)


def parse_combined_whatsapp_export(text: str, default_source: str) -> list[ParsedMessage]:
    return _parse_whatsapp_lines(text.splitlines(), default_source, allow_source_markers=True)


def filter_recent(messages: Iterable[ParsedMessage], lookback_days: int, now: datetime) -> list[ParsedMessage]:
    if lookback_days <= 0:
        return list(messages)
    threshold = now - timedelta(days=lookback_days)
    return [msg for msg in messages if msg.timestamp >= threshold]
