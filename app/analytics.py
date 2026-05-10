from __future__ import annotations

from datetime import datetime


def budget_bucket(min_v: object, max_v: object) -> str:
    try:
        val = (float(min_v) + float(max_v)) / 2
    except Exception:
        return "Unknown"
    if val < 3_000_000:
        return "<30L"
    if val < 8_000_000:
        return "30L-80L"
    if val < 15_000_000:
        return "80L-1.5Cr"
    return ">1.5Cr"


def month_bucket(value: datetime) -> str:
    return value.strftime("%Y-%m")


def week_bucket(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def column_letter(index: int) -> str:
    if index < 1:
        raise ValueError("Column index must be 1-based")
    chars: list[str] = []
    while index:
        index, remainder = divmod(index - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))
