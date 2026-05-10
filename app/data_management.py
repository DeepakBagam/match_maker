from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .pipeline import _build_final_validation_rows, _load_config, _write_outputs
from .schemas import (
    MANUAL_COLUMNS,
    RAW_COLUMNS,
    StructuredLead,
)
from .matcher import compute_matches, compute_priority, demand_summary, supply_summary, top_leads
from .sheets_client import PROCESSED_MESSAGE_COLUMNS


@dataclass
class ClearResult:
    mode: str
    deleted_rows: int
    remaining_rows: int
    from_date: str | None
    to_date: str | None


def parse_optional_date(value: str | None) -> date | None:
    if value is None or not str(value).strip():
        return None
    return date.fromisoformat(str(value))


def _date_in_range(value: date | None, from_date: date | None, to_date: date | None) -> bool:
    if value is None:
        return False
    if from_date is not None and value < from_date:
        return False
    if to_date is not None and value > to_date:
        return False
    return True


def _lead_date(lead: StructuredLead) -> date | None:
    raw = lead.values.get("Date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def _structured_rows_from_sheet(client) -> list[StructuredLead]:
    client.ensure_structure()
    rows = client.read_structured()
    payload_rows = rows[1:] if rows else []
    return [StructuredLead.from_row(row) for row in payload_rows if any(str(cell).strip() for cell in row)]


def get_structured_dataset(
    client,
    *,
    tab: str = "Structured Data",
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    payload = client.get_table_page(
        tab,
        limit=limit,
        offset=offset,
        from_date=from_date.isoformat() if from_date else None,
        to_date=to_date.isoformat() if to_date else None,
    )
    payload["tab"] = tab
    payload["filters"] = {
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
    }
    return payload


def _keep_row_by_timestamp(row: list[str], index: int, from_date: date | None, to_date: date | None) -> bool:
    raw_value = row[index] if index < len(row) else ""
    if not raw_value:
        return True
    try:
        row_date = datetime.fromisoformat(raw_value).date()
    except ValueError:
        return True
    return not _date_in_range(row_date, from_date, to_date)


def _rewrite_tracking_tabs(client, from_date: date | None, to_date: date | None, clear_all: bool) -> None:
    if clear_all:
        client.replace_rows("Raw Data", RAW_COLUMNS, [])
        client.replace_rows("Processed Messages", PROCESSED_MESSAGE_COLUMNS, [])
        client.replace_rows("Manual Entries", MANUAL_COLUMNS, [])
        return

    raw_rows = client.get_table("Raw Data")
    processed_rows = client.get_table("Processed Messages")
    manual_rows = client.get_table("Manual Entries")

    filtered_raw = [row for row in raw_rows[1:] if _keep_row_by_timestamp(row, 3, from_date, to_date)]
    filtered_processed = [row for row in processed_rows[1:] if _keep_row_by_timestamp(row, 2, from_date, to_date)]
    filtered_manual = [row for row in manual_rows[1:] if _keep_row_by_timestamp(row, 0, from_date, to_date)]

    client.replace_rows("Raw Data", RAW_COLUMNS, filtered_raw)
    client.replace_rows("Processed Messages", PROCESSED_MESSAGE_COLUMNS, filtered_processed)
    client.replace_rows("Manual Entries", MANUAL_COLUMNS, filtered_manual)


def _rewrite_analytics_tabs(client, leads: list[StructuredLead]) -> None:
    config, weights = _load_config(client)
    validation_sample_size = max(0, int(config.get("validation_sample_size", 50)))
    validation_rows = leads[:validation_sample_size]

    now = datetime.now()
    matches = compute_matches(leads, weights, float(config.get("match_threshold", 40)), now)
    match_validation_size = max(0, int(config.get("match_validation_sample_size", 15)))
    match_validation_rows = matches[:match_validation_size]

    compute_priority(leads, matches, weights, now)

    top_count = int(config.get("top_leads_count", 10))
    top = top_leads(leads, top_count)
    top_validation_size = max(0, int(config.get("top_leads_validation_size", 10)))
    top_validation_rows = top[:top_validation_size]
    demand = demand_summary(leads)
    supply = supply_summary(leads)
    final_validation_rows = _build_final_validation_rows([], leads, matches, top, demand, supply)

    _write_outputs(
        client,
        leads,
        validation_rows,
        final_validation_rows,
        matches,
        match_validation_rows,
        demand,
        supply,
        top,
        top_validation_rows,
    )


def clear_structured_data(client, mode: str, from_date: date | None = None, to_date: date | None = None) -> ClearResult:
    if mode not in {"range", "all"}:
        raise ValueError("mode must be 'range' or 'all'")
    if mode == "range" and from_date is None and to_date is None:
        raise ValueError("from_date or to_date is required for range clear")
    if from_date and to_date and from_date > to_date:
        raise ValueError("from_date cannot be after to_date")

    client.ensure_structure()
    leads = _structured_rows_from_sheet(client)
    original_count = len(leads)
    clear_all = mode == "all"

    if clear_all:
        remaining = []
    else:
        remaining = [lead for lead in leads if not _date_in_range(_lead_date(lead), from_date, to_date)]

    deleted_rows = original_count - len(remaining)

    _rewrite_analytics_tabs(client, remaining)
    _rewrite_tracking_tabs(client, from_date, to_date, clear_all)

    return ClearResult(
        mode=mode,
        deleted_rows=deleted_rows,
        remaining_rows=len(remaining),
        from_date=from_date.isoformat() if from_date else None,
        to_date=to_date.isoformat() if to_date else None,
    )
