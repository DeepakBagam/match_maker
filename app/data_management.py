from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .pipeline import _build_final_validation_rows, _load_config, _write_outputs
from .schemas import (
    StructuredLead,
)
from .matcher import compute_matches, compute_priority, demand_summary, supply_summary, top_leads


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
    if hasattr(client, "read_structured_leads_fast"):
        return client.read_structured_leads_fast()
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
    search: str = "",
    column_filters: dict[str, str] | None = None,
    sort_column: str = "",
    sort_direction: str = "desc",
) -> dict[str, Any]:
    payload = client.get_table_page(
        tab,
        limit=limit,
        offset=offset,
        from_date=from_date.isoformat() if from_date else None,
        to_date=to_date.isoformat() if to_date else None,
        search=search,
        column_filters=column_filters,
        sort_column=sort_column,
        sort_direction=sort_direction,
    )
    payload["tab"] = tab
    payload["filters"] = {
        "from_date": from_date.isoformat() if from_date else None,
        "to_date": to_date.isoformat() if to_date else None,
        "search": search.strip(),
        "column_filters": {str(key): str(value).strip() for key, value in (column_filters or {}).items() if str(value).strip()},
        "sort_column": sort_column.strip(),
        "sort_direction": sort_direction.strip().lower() or "desc",
    }
    return payload


def _rewrite_tracking_tabs(client, from_date: date | None, to_date: date | None, clear_all: bool) -> None:
    if clear_all:
        client.clear_tab("Raw Data")
        client.clear_tab("Processed Messages")
        client.clear_tab("Manual Entries")
        return
    from_ts = f"{from_date.isoformat()} 00:00:00" if from_date else None
    to_ts = f"{to_date.isoformat()} 23:59:59" if to_date else None
    client.delete_rows_by_text_range("Raw Data", "Timestamp", from_ts, to_ts)
    client.delete_rows_by_text_range("Processed Messages", "Timestamp", from_ts, to_ts)
    client.delete_rows_by_text_range("Manual Entries", "Submitted At", from_ts, to_ts)


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
    original_count = client.count_rows("Structured Data")
    clear_all = mode == "all"

    if clear_all:
        client.clear_tab("Structured Data")
    else:
        client.delete_rows_by_text_range(
            "Structured Data",
            "Date",
            from_date.isoformat() if from_date else None,
            to_date.isoformat() if to_date else None,
        )

    remaining = _structured_rows_from_sheet(client)

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
