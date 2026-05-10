from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .datetime_utils import parse_datetime
from .schemas import StructuredLead


@dataclass
class DedupResult:
    leads: list[StructuredLead]
    new_count: int
    duplicate_count: int


def _as_datetime(value: str) -> datetime:
    return parse_datetime(value)


def _text_value(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _lead_signature(lead: StructuredLead) -> str:
    values = lead.values
    fields = (
        "Type",
        "Transaction Type",
        "Location",
        "Property Type",
        "BHK",
        "Budget_Min",
        "Budget_Max",
    )
    return "|".join(_text_value(values.get(field, "")) for field in fields)


def _is_duplicate(existing: StructuredLead, incoming: StructuredLead, dedup_days: int) -> bool:
    e = existing.values
    i = incoming.values

    existing_phone = _text_value(e.get("Contact Number", ""))
    incoming_phone = _text_value(i.get("Contact Number", ""))

    if incoming_phone:
        if existing_phone != incoming_phone:
            return False
        if e.get("Raw Message", "") != i.get("Raw Message", ""):
            return False
        if _lead_signature(existing) != _lead_signature(incoming):
            return False
        first = _as_datetime(str(e.get("First Seen")))
        current = _as_datetime(str(i.get("First Seen")))
        return abs((current - first).days) <= dedup_days

    fallback_existing = f"{e.get('Name','')}|{e.get('Date','')}|{e.get('Raw Message','')}"
    fallback_incoming = f"{i.get('Name','')}|{i.get('Date','')}|{i.get('Raw Message','')}"
    if fallback_existing != fallback_incoming:
        return False
    first = _as_datetime(str(e.get("First Seen")))
    current = _as_datetime(str(i.get("First Seen")))
    return abs((current - first).days) <= dedup_days


def _candidate_key(lead: StructuredLead) -> tuple[str, str, str, str] | tuple[str, str]:
    values = lead.values
    phone = _text_value(values.get("Contact Number", ""))
    if phone:
        return (
            "phone",
            phone,
            _text_value(values.get("Raw Message", "")),
            _lead_signature(lead),
        )
    return (
        "fallback",
        f"{values.get('Name', '')}|{values.get('Date', '')}|{values.get('Raw Message', '')}",
    )


def _find_duplicate_candidate(
    buckets: dict[tuple[str, ...], list[StructuredLead]],
    candidate: StructuredLead,
    dedup_days: int,
) -> StructuredLead | None:
    for existing in buckets.get(_candidate_key(candidate), []):
        if _is_duplicate(existing, candidate, dedup_days):
            return existing
    return None


def deduplicate(existing: list[StructuredLead], incoming: list[StructuredLead], dedup_days: int = 1) -> DedupResult:
    merged = [StructuredLead(dict(lead.values)) for lead in existing]
    duplicate_count = 0
    new_count = 0
    buckets: dict[tuple[str, ...], list[StructuredLead]] = defaultdict(list)

    for lead in merged:
        buckets[_candidate_key(lead)].append(lead)

    for candidate in incoming:
        found = _find_duplicate_candidate(buckets, candidate, dedup_days)

        if found:
            duplicate_count += 1
            found.values["Repeat Count"] = int(found.values.get("Repeat Count", 1)) + 1
            found.values["Last Seen"] = candidate.values.get("Last Seen")
            continue

        new_count += 1
        merged.append(candidate)
        buckets[_candidate_key(candidate)].append(candidate)

    return DedupResult(leads=merged, new_count=new_count, duplicate_count=duplicate_count)


def recency_score(last_seen_iso: str, now: datetime, max_days: int = 14) -> float:
    """Score recency with stronger decay - recent leads get much higher scores."""
    dt = _as_datetime(last_seen_iso)
    age_days = max((now - dt).days, 0)
    
    # Exponential decay: very recent leads get near 1.0, older leads drop fast
    if age_days == 0:
        return 1.0
    elif age_days <= 3:
        return 0.9
    elif age_days <= 7:
        return 0.7
    elif age_days <= 14:
        return 0.4
    else:
        return max(0.0, 0.2 - (age_days - 14) * 0.02)


def completeness_score(lead: StructuredLead) -> float:
    fields = [
        "Type",
        "Transaction Type",
        "Location",
        "Property Type",
        "BHK",
        "Budget_Min",
        "Budget_Max",
        "Contact Number",
    ]
    present = sum(1 for f in fields if _text_value(lead.values.get(f, "")))
    return present / len(fields)
