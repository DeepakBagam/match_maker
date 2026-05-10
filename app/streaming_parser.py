"""
Streaming parser for large WhatsApp exports.
Processes files in chunks to avoid memory issues.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Iterator

from dateutil import parser as dt_parser

from .schemas import ParsedMessage


_MESSAGE_START_PATTERNS = [
    re.compile(
        r"^\[(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}),\s*(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?)\]\s*(?P<sender>[^:]+):\s?(?P<body>.*)$"
    ),
    re.compile(
        r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4}),?\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?)\\s+-\s+(?P<sender>[^:]+):\s?(?P<body>.*)$"
    ),
]


def _parse_timestamp(date_text: str, time_text: str) -> datetime:
    return dt_parser.parse(f"{date_text} {time_text}", dayfirst=True)


def _match_message_start(line: str):
    cleaned = line.strip()
    for pattern in _MESSAGE_START_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return match
    return None


def stream_parse_whatsapp_file(file_path: str, source: str, chunk_size: int = 10000) -> Iterator[list[ParsedMessage]]:
    """
    Stream parse a large WhatsApp export file in chunks.
    
    Args:
        file_path: Path to the WhatsApp export .txt file
        source: Source identifier for the messages
        chunk_size: Number of messages to yield per chunk
    
    Yields:
        Lists of ParsedMessage objects in chunks
    """
    current: ParsedMessage | None = None
    chunk: list[ParsedMessage] = []
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            match = _match_message_start(line)
            
            if match:
                # Save previous message
                if current is not None:
                    chunk.append(current)
                    
                    # Yield chunk if it reaches chunk_size
                    if len(chunk) >= chunk_size:
                        yield chunk
                        chunk = []
                
                # Start new message
                timestamp = _parse_timestamp(match.group("date"), match.group("time"))
                current = ParsedMessage(
                    timestamp=timestamp,
                    sender=match.group("sender").strip(),
                    message=match.group("body").strip(),
                    source=source,
                    raw_message=match.group("body").strip(),
                )
            elif current is not None:
                # Continuation of previous message
                current.message = f"{current.message}\n{line.rstrip()}"
                current.raw_message = current.message
        
        # Don't forget the last message
        if current is not None:
            chunk.append(current)
        
        # Yield remaining messages
        if chunk:
            yield chunk


def estimate_message_count(file_path: str) -> int:
    """
    Quickly estimate the number of messages in a WhatsApp export file.
    
    Args:
        file_path: Path to the WhatsApp export .txt file
    
    Returns:
        Estimated message count
    """
    count = 0
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if _match_message_start(line):
                count += 1
    return count
