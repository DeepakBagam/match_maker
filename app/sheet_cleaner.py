"""
Utility to clean data before writing to the database store.
Removes emojis, control characters, and normalizes text.
"""

import re

# Comprehensive emoji pattern
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"  # flags (iOS)
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002702-\U000027B0"  # Dingbats
    "\U000024C2-\U0001F251" 
    "]+",
    flags=re.UNICODE
)

# Control characters and zero-width characters
CONTROL_CHARS = re.compile(r'[\x00-\x1F\x7F-\x9F\u200B-\u200D\uFEFF]')

# Multiple spaces
MULTI_SPACE = re.compile(r'\s+')


def remove_emojis(text: str) -> str:
    """Remove all emojis from text."""
    if not text:
        return text
    return EMOJI_PATTERN.sub('', str(text))


def remove_control_chars(text: str) -> str:
    """Remove control characters and zero-width spaces."""
    if not text:
        return text
    return CONTROL_CHARS.sub('', str(text))


def normalize_whitespace(text: str) -> str:
    """Normalize multiple spaces to single space."""
    if not text:
        return text
    return MULTI_SPACE.sub(' ', str(text)).strip()


def clean_for_sheets(value: object) -> object:
    """
    Clean a value before writing to the database store.
    - Removes emojis
    - Removes control characters
    - Normalizes whitespace
    - Preserves numbers and None values
    """
    if value is None:
        return ""
    
    if isinstance(value, (int, float)):
        return value
    
    text = str(value)
    text = remove_emojis(text)
    text = remove_control_chars(text)
    text = normalize_whitespace(text)
    
    return text


def clean_row(row: list[object]) -> list[object]:
    """Clean all values in a row."""
    return [clean_for_sheets(val) for val in row]


def clean_rows(rows: list[list[object]]) -> list[list[object]]:
    """Clean all rows."""
    return [clean_row(row) for row in rows]
