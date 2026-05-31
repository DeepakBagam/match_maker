from __future__ import annotations

from threading import Lock
from datetime import date, datetime, timedelta
from typing import Any
import re

from .db_client import DEFAULT_CONFIG, REQUIRED_TABS, _STRUCTURED_ONLY_LEAD_PREFIX, _column_name, _table_name
from .schemas import MATCH_COLUMNS, STRUCTURED_COLUMNS, StructuredLead

GLIDE_CACHE_TTL_SECONDS = 300

GLIDE_VIEW_COLUMNS = [
    "lead_id",
    "name",
    "phone",
    "lead_type",
    "entry_type",
    "area",
    "property_type",
    "bhk",
    "budget",
    "transaction_type",
    "priority",
    "match_count",
    "strength",
    "last_interaction_date",
    "best_match_summary",
    "match_reason",
    "lead_date",
    "cleaned_message",
    "raw_message",
    "status",
    "notes",
    "next_follow_up",
    "next_action",
    "timing",
    "calc_active_within_120_days",
    "calc_follow_up_pending",
    "calc_priority_qualified",
    "calc_recent_interaction",
    "calc_in_today_view",
    "last_seen_at",
    "updated_at",
    "confidence_score",
    "extraction_flags",
    "first_seen_at",
    "repeat_count",
    "source",
    "project_name",
    "match_1_property_summary",
    "match_1_broker_name",
    "match_1_broker_phone",
    "match_2_property_summary",
    "match_2_broker_name",
    "match_2_broker_phone",
    "match_3_property_summary",
    "match_3_broker_name",
    "match_3_broker_phone",
]

_GLIDE_CACHE: dict[str, Any] = {"built_at": None, "rows": None, "client_key": None}
_GLIDE_CACHE_LOCK = Lock()


def invalidate_glide_cache() -> None:
    with _GLIDE_CACHE_LOCK:
        _GLIDE_CACHE["built_at"] = None
        _GLIDE_CACHE["rows"] = None
        _GLIDE_CACHE["client_key"] = None


def _client_cache_key(client) -> str:
    if hasattr(client, "database_target"):
        return f"{getattr(client, 'backend', 'db')}:{getattr(client, 'database_target', '')}"
    return f"memory:{id(client)}"


def _safe_str(value: object) -> str:
    return "" if value is None else str(value).strip()


def _normalize_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _normalize_date(value: object) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    if "T" in raw or " " in raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def _normalize_time(value: object) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        pass
    if len(raw) >= 5 and raw[2] == ":":
        return raw[:5]
    return ""


def _normalize_numeric(value: object) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return ""
    return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")


def _display_property_type(property_type: object, bhk: object, *texts: object) -> str:
    normalized = _safe_str(property_type)
    lowered = normalized.lower()
    if lowered in {"plot", "land"} and _safe_str(bhk):
        blob = " ".join(_safe_str(text) for text in texts).lower()
        if re.search(r"\b(?:bungalow|bunglow|villa|independent house|row house|duplex)\b", blob):
            return "Villa"
        if re.search(r"\b(?:flat|apartment)\b", blob):
            return "Apartment"
    return normalized


def _parse_datetime(value: object) -> datetime | None:
    raw = _safe_str(value)
    if not raw:
        return None
    for candidate in (raw, raw.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    try:
        return datetime.combine(date.fromisoformat(raw), datetime.min.time())
    except ValueError:
        return None


def get_glide_filter_config(client) -> dict[str, float]:
    config_values = client.get_key_values("Config") if hasattr(client, "get_key_values") else {}
    config = {**DEFAULT_CONFIG, **config_values}
    return {
        "activity_window_days": float(config.get("glide_activity_window_days", DEFAULT_CONFIG["glide_activity_window_days"])),
        "recent_interaction_days": float(config.get("glide_recent_interaction_days", DEFAULT_CONFIG["glide_recent_interaction_days"])),
        "priority_qualified_score": float(config.get("glide_priority_qualified_score", DEFAULT_CONFIG["glide_priority_qualified_score"])),
    }


def _priority_badge(lead: StructuredLead, priority_qualified_score: float) -> str:
    score = float(lead.values.get("Priority Score") or 0)
    if score >= 80:
        return "HIGH"
    if score >= priority_qualified_score:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "UNRANKED"


def _strength_label(lead: StructuredLead, priority_qualified_score: float) -> str:
    score = float(lead.values.get("Priority Score") or 0)
    if score >= 80:
        return "Strong"
    if score >= priority_qualified_score:
        return "Moderate"
    if score > 0:
        return "Developing"
    return "Pending"


def _budget_text(lead: StructuredLead) -> str:
    minimum = _normalize_numeric(lead.values.get("Budget_Min"))
    maximum = _normalize_numeric(lead.values.get("Budget_Max"))
    if minimum and maximum:
        return minimum if minimum == maximum else f"{minimum}-{maximum}"
    return _safe_str(lead.values.get("Budget Range"))


def _budget_text_from_values(values: dict[str, object]) -> str:
    minimum = _normalize_numeric(values.get("Budget_Min"))
    maximum = _normalize_numeric(values.get("Budget_Max"))
    if minimum and maximum:
        return minimum if minimum == maximum else f"{minimum}-{maximum}"
    return _safe_str(values.get("Budget Range"))


def _match_property_summary(match_row: dict[str, str]) -> str:
    parts = [
        _safe_str(match_row.get("Property Type")),
        _safe_str(match_row.get("Location")),
        f"{_safe_str(match_row.get('BHK'))} BHK" if _safe_str(match_row.get("BHK")) else "",
    ]
    budget = _safe_str(match_row.get("Seller Budget") or match_row.get("Buyer Budget"))
    if budget:
        parts.append(f"Budget {budget}")
    return " | ".join(part for part in parts if part)


def _lead_recency_value(*values: object) -> str:
    best: datetime | None = None
    for value in values:
        parsed = _parse_datetime(value)
        if parsed and (best is None or parsed > best):
            best = parsed
    return best.isoformat(sep=" ", timespec="seconds") if best else ""


def _lead_sort_key(item: dict[str, str]) -> tuple[float, float, int, str, str]:
    recency = _parse_datetime(item.get("last_seen_at") or item.get("lead_date") or item.get("last_interaction_date"))
    recency_ts = recency.timestamp() if recency else float("-inf")
    try:
        match_count = float(item.get("match_count") or 0)
    except Exception:
        match_count = 0.0
    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNRANKED": 3}.get(item.get("priority", ""), 4)
    return (-recency_ts, -match_count, priority_rank, item.get("name", "").lower(), item.get("lead_id", ""))


def _load_structured_leads(client) -> list[StructuredLead]:
    client.ensure_structure()
    rows = client.get_table("Structured Data")
    payload_rows = rows[1:] if rows else []
    return [StructuredLead.from_row(row) for row in payload_rows if any(_safe_str(cell) for cell in row)]


def _load_glide_base_rows(client) -> list[dict[str, object]]:
    if not hasattr(client, "_connect"):
        leads = _load_structured_leads(client)
        return [dict(lead.values) for lead in leads]

    client.ensure_structure()
    table_name = _table_name("Structured Data")
    columns = [
        "Lead_ID",
        "Name",
        "Contact Number",
        "Type",
        "Transaction Type",
        "Location",
        "BHK",
        "Budget_Min",
        "Budget_Max",
        "Budget Range",
        "Type",
        "Priority Score",
        "Date",
        "Last Seen",
        "Cleaned Message",
        "Raw Message",
    ]
    select_columns = ", ".join(
        f'"{_column_name(column)}" AS "{column}"'
        for column in columns
    )
    sql = f'''
        SELECT {select_columns}
        FROM "{table_name}"
        WHERE COALESCE("{_column_name("Lead_ID")}", '') <> ''
          AND COALESCE("{_column_name("Type")}", '') IN ('Buyer', 'Seller')
        ORDER BY row_id DESC
    '''
    with client._connect() as connection:
        rows = connection.execute(sql).fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def _load_match_summary_from_rows(
    match_rows: list[dict[str, str]],
    counterparty_recency_by_lead: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    lead_map: dict[str, dict[str, Any]] = {}
    counterparty_recency_by_lead = counterparty_recency_by_lead or {}
    for row in match_rows:
        for lead_key, counterparty_key, broker_name_key, broker_phone_key, budget_key in (
            ("Buyer Lead_ID", "Seller Lead_ID", "Seller Name", "Seller Phone", "Seller Budget"),
            ("Seller Lead_ID", "Buyer Lead_ID", "Buyer Name", "Buyer Phone", "Buyer Budget"),
        ):
            lead_id = _safe_str(row.get(lead_key, ""))
            if not lead_id:
                continue
            bucket = lead_map.setdefault(lead_id, {"match_count": 0, "slots": []})
            bucket["match_count"] += 1
            counterparty_lead_id = _safe_str(row.get(counterparty_key, ""))
            bucket["slots"].append(
                {
                    "property_summary": _match_property_summary(
                        {
                            "Property Type": row.get("Property Type", ""),
                            "Location": row.get("Location", ""),
                            "BHK": row.get("BHK", ""),
                            "Seller Budget": row.get(budget_key, ""),
                            "Buyer Budget": row.get(budget_key, ""),
                        }
                    ),
                    "broker_name": _safe_str(row.get(broker_name_key, "")),
                    "broker_phone": _safe_str(row.get(broker_phone_key, "")),
                    "match_reason": _safe_str(row.get("Match Reason", "")),
                    "counterparty_recency": counterparty_recency_by_lead.get(counterparty_lead_id, ""),
                    "matched_at": _safe_str(row.get("Matched At", "")),
                    "match_score": _safe_str(row.get("Match Score", "")),
                }
            )
    for bucket in lead_map.values():
        bucket["slots"].sort(
            key=lambda slot: (
                slot.get("counterparty_recency", ""),
                float(slot.get("match_score") or 0),
                slot.get("matched_at", ""),
                slot.get("broker_name", "").lower(),
            ),
            reverse=True,
        )
        bucket["slots"] = bucket["slots"][:3]
    return lead_map


def _load_match_summary_by_lead(client) -> dict[str, dict[str, Any]]:
    matches_table = _table_name("Matches")
    lead_map: dict[str, dict[str, Any]] = {}
    if not hasattr(client, "_connect"):
        dataset = client.get_table_rows("Matches")
        rows = dataset.get("rows", [])
        leads = _load_structured_leads(client)
        counterparty_recency_by_lead = {
            _safe_str(lead.values.get("Lead_ID")): _lead_recency_value(lead.values.get("Last Seen"), lead.values.get("Date"))
            for lead in leads
        }
        normalized_rows = [{column: _safe_str(row.get(column, "")) for column in MATCH_COLUMNS} for row in rows]
        return _load_match_summary_from_rows(normalized_rows, counterparty_recency_by_lead)
    sql = f'''
        WITH normalized AS (
            SELECT
                source_match."{_column_name("Buyer Lead_ID")}" AS lead_id,
                source_match."{_column_name("Seller Lead_ID")}" AS counterparty_lead_id,
                source_match."{_column_name("Seller Name")}" AS broker_name,
                source_match."{_column_name("Seller Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Seller Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Seller Lead_ID")}"
            WHERE COALESCE(source_match."{_column_name("Buyer Lead_ID")}", '') <> ''
            UNION ALL
            SELECT
                source_match."{_column_name("Seller Lead_ID")}" AS lead_id,
                source_match."{_column_name("Buyer Lead_ID")}" AS counterparty_lead_id,
                source_match."{_column_name("Buyer Name")}" AS broker_name,
                source_match."{_column_name("Buyer Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Buyer Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Buyer Lead_ID")}"
            WHERE COALESCE(source_match."{_column_name("Seller Lead_ID")}", '') <> ''
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER (PARTITION BY lead_id) AS match_count,
                ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY counterparty_recency DESC, match_score DESC, matched_at DESC, broker_name ASC) AS rn
            FROM normalized
        )
        SELECT
            lead_id,
            broker_name,
            broker_phone,
            property_type,
            location,
            bhk,
            budget,
            match_reason,
            counterparty_recency,
            match_count,
            rn
        FROM ranked
        WHERE rn <= 3
        ORDER BY lead_id, rn
    '''
    with client._connect() as connection:
        rows = connection.execute(sql).fetchall()

    for row in rows:
        lead_id = _safe_str(row["lead_id"])
        if not lead_id:
            continue
        bucket = lead_map.setdefault(lead_id, {"match_count": 0, "slots": []})
        try:
            bucket["match_count"] = int(row["match_count"] or 0)
        except Exception:
            bucket["match_count"] = 0
        property_summary = " | ".join(
            part
            for part in [
                _safe_str(row["property_type"]),
                _safe_str(row["location"]),
                f"{_safe_str(row['bhk'])} BHK" if _safe_str(row["bhk"]) else "",
                f"Budget {_safe_str(row['budget'])}" if _safe_str(row["budget"]) else "",
            ]
            if part
        )
        bucket["slots"].append(
            {
                "property_summary": property_summary,
                "broker_name": _safe_str(row["broker_name"]),
                "broker_phone": _safe_str(row["broker_phone"]),
                "match_reason": _safe_str(row["match_reason"]),
                "counterparty_recency": _safe_str(row["counterparty_recency"]),
            }
        )
    return lead_map


def _empty_match_detail() -> dict[str, str]:
    return {
        "match_count": "0",
        "best_match_summary": "",
        "match_reason": "",
        "match_1_property_summary": "",
        "match_1_broker_name": "",
        "match_1_broker_phone": "",
        "match_2_property_summary": "",
        "match_2_broker_name": "",
        "match_2_broker_phone": "",
        "match_3_property_summary": "",
        "match_3_broker_name": "",
        "match_3_broker_phone": "",
    }


def _match_detail_from_bucket(bucket: dict[str, Any]) -> dict[str, str]:
    detail = _empty_match_detail()
    if not bucket:
        return detail

    slots = list(bucket.get("slots", []))[:3]
    detail["match_count"] = str(bucket.get("match_count", 0))
    if slots:
        detail["best_match_summary"] = slots[0].get("property_summary", "")
        detail["match_reason"] = slots[0].get("match_reason", "")
    for index in range(3):
        slot = slots[index] if index < len(slots) else {}
        offset = index + 1
        detail[f"match_{offset}_property_summary"] = _safe_str(slot.get("property_summary", ""))
        detail[f"match_{offset}_broker_name"] = _safe_str(slot.get("broker_name", ""))
        detail[f"match_{offset}_broker_phone"] = _safe_str(slot.get("broker_phone", ""))
    return detail


def _priority_rank_from_score(score: float, priority_qualified_score: float) -> int:
    if score >= 80:
        return 0
    if score >= priority_qualified_score:
        return 1
    if score > 0:
        return 2
    return 3


def _priority_label_from_score(score: float, priority_qualified_score: float) -> str:
    return ("HIGH", "MEDIUM", "LOW", "UNRANKED")[_priority_rank_from_score(score, priority_qualified_score)]


def _strength_label_from_score(score: float, priority_qualified_score: float) -> str:
    if score >= 80:
        return "Strong"
    if score >= priority_qualified_score:
        return "Moderate"
    if score > 0:
        return "Developing"
    return "Pending"


def _load_match_counts_for_leads(client, lead_ids: list[str]) -> dict[str, int]:
    normalized = [lead_id.strip() for lead_id in lead_ids if lead_id and lead_id.strip()]
    if not normalized:
        return {}

    if not hasattr(client, "_connect"):
        match_map = _load_match_summary_by_lead(client)
        return {lead_id: int(match_map.get(lead_id, {}).get("match_count", 0)) for lead_id in normalized}

    matches_table = _table_name("Matches")
    buyer_column = _column_name("Buyer Lead_ID")
    seller_column = _column_name("Seller Lead_ID")
    placeholders = ", ".join("?" for _ in normalized)
    sql = f'''
        SELECT lead_id, SUM(match_count) AS match_count
        FROM (
            SELECT "{buyer_column}" AS lead_id, COUNT(*) AS match_count
            FROM "{matches_table}"
            WHERE "{buyer_column}" IN ({placeholders})
            GROUP BY "{buyer_column}"
            UNION ALL
            SELECT "{seller_column}" AS lead_id, COUNT(*) AS match_count
            FROM "{matches_table}"
            WHERE "{seller_column}" IN ({placeholders})
            GROUP BY "{seller_column}"
        )
        GROUP BY lead_id
    '''
    with client._connect() as connection:
        rows = connection.execute(sql, [*normalized, *normalized]).fetchall()
    return {str(row["lead_id"]).strip(): int(row["match_count"] or 0) for row in rows if row["lead_id"]}


def _load_match_detail_for_leads(client, lead_ids: list[str]) -> dict[str, dict[str, str]]:
    normalized = [lead_id.strip() for lead_id in lead_ids if lead_id and lead_id.strip()]
    if not normalized:
        return {}

    if not hasattr(client, "_connect"):
        match_map = _load_match_summary_by_lead(client)
        return {lead_id: _match_detail_from_bucket(match_map.get(lead_id, {})) for lead_id in normalized}

    matches_table = _table_name("Matches")
    placeholders = ", ".join("?" for _ in normalized)
    sql = f'''
        WITH normalized AS (
            SELECT
                source_match."{_column_name("Buyer Lead_ID")}" AS lead_id,
                source_match."{_column_name("Seller Lead_ID")}" AS counterparty_lead_id,
                source_match."{_column_name("Seller Name")}" AS broker_name,
                source_match."{_column_name("Seller Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Seller Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Seller Lead_ID")}"
            WHERE source_match."{_column_name("Buyer Lead_ID")}" IN ({placeholders})
            UNION ALL
            SELECT
                source_match."{_column_name("Seller Lead_ID")}" AS lead_id,
                source_match."{_column_name("Buyer Lead_ID")}" AS counterparty_lead_id,
                source_match."{_column_name("Buyer Name")}" AS broker_name,
                source_match."{_column_name("Buyer Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Buyer Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Buyer Lead_ID")}"
            WHERE source_match."{_column_name("Seller Lead_ID")}" IN ({placeholders})
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER (PARTITION BY lead_id) AS match_count,
                ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY counterparty_recency DESC, match_score DESC, matched_at DESC, broker_name ASC) AS rn
            FROM normalized
        )
        SELECT
            lead_id,
            broker_name,
            broker_phone,
            property_type,
            location,
            bhk,
            budget,
            match_reason,
            match_count,
            counterparty_recency
        FROM ranked
        WHERE rn <= 3
        ORDER BY lead_id, rn
    '''
    with client._connect() as connection:
        rows = connection.execute(sql, [*normalized, *normalized]).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        lead_id = _safe_str(row["lead_id"])
        if not lead_id:
            continue
        bucket = grouped.setdefault(lead_id, {"match_count": 0, "slots": []})
        try:
            bucket["match_count"] = int(row["match_count"] or 0)
        except Exception:
            bucket["match_count"] = 0
        bucket["slots"].append(
            {
                "property_summary": " | ".join(
                    part
                    for part in [
                        _safe_str(row["property_type"]),
                        _safe_str(row["location"]),
                        f"{_safe_str(row['bhk'])} BHK" if _safe_str(row["bhk"]) else "",
                        f"Budget {_safe_str(row['budget'])}" if _safe_str(row["budget"]) else "",
                    ]
                    if part
                ),
                "broker_name": _safe_str(row["broker_name"]),
                "broker_phone": _safe_str(row["broker_phone"]),
                "match_reason": _safe_str(row["match_reason"]),
            }
        )
    return {lead_id: _match_detail_from_bucket(bucket) for lead_id, bucket in grouped.items()}


def _load_match_detail_for_lead(client, lead_id: str) -> dict[str, str]:
    normalized_lead_id = lead_id.strip()
    if not normalized_lead_id:
        return _empty_match_detail()
    return _load_match_detail_for_leads(client, [normalized_lead_id]).get(normalized_lead_id, _empty_match_detail())


def get_glide_lead_matches(client, lead_id: str, *, limit: int = 3, offset: int = 0) -> dict[str, Any]:
    normalized_lead_id = _safe_str(lead_id)
    if not normalized_lead_id:
        return {"lead_id": "", "row_count": 0, "limit": limit, "offset": offset, "rows": []}

    limit = max(1, min(int(limit or 3), 50))
    offset = max(0, int(offset or 0))

    if not hasattr(client, "_connect"):
        dataset = client.get_table_rows("Matches")
        rows = dataset.get("rows", [])
        slots: list[dict[str, str]] = []
        for row in rows:
            for lead_key, broker_name_key, broker_phone_key, budget_key in (
                ("Buyer Lead_ID", "Seller Name", "Seller Phone", "Seller Budget"),
                ("Seller Lead_ID", "Buyer Name", "Buyer Phone", "Buyer Budget"),
            ):
                if _safe_str(row.get(lead_key)) != normalized_lead_id:
                    continue
                slots.append(
                    {
                        "property_summary": _match_property_summary(
                            {
                                "Property Type": row.get("Property Type", ""),
                                "Location": row.get("Location", ""),
                                "BHK": row.get("BHK", ""),
                                "Seller Budget": row.get(budget_key, ""),
                                "Buyer Budget": row.get(budget_key, ""),
                            }
                        ),
                        "broker_name": _safe_str(row.get(broker_name_key, "")),
                        "broker_phone": _safe_str(row.get(broker_phone_key, "")),
                        "match_reason": _safe_str(row.get("Match Reason", "")),
                        "match_score": _safe_str(row.get("Match Score", "")),
                        "matched_at": _safe_str(row.get("Matched At", "")),
                    }
                )
        slots.sort(key=lambda slot: (float(slot.get("match_score") or 0), slot.get("matched_at", "")), reverse=True)
        return {
            "lead_id": normalized_lead_id,
            "row_count": len(slots),
            "limit": limit,
            "offset": offset,
            "rows": slots[offset:offset + limit],
        }

    matches_table = _table_name("Matches")
    sql = f'''
        WITH normalized AS (
            SELECT
                source_match."{_column_name("Buyer Lead_ID")}" AS lead_id,
                source_match."{_column_name("Seller Name")}" AS broker_name,
                source_match."{_column_name("Seller Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Seller Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Seller Lead_ID")}"
            WHERE source_match."{_column_name("Buyer Lead_ID")}" = ?
            UNION ALL
            SELECT
                source_match."{_column_name("Seller Lead_ID")}" AS lead_id,
                source_match."{_column_name("Buyer Name")}" AS broker_name,
                source_match."{_column_name("Buyer Phone")}" AS broker_phone,
                source_match."{_column_name("Property Type")}" AS property_type,
                source_match."{_column_name("Location")}" AS location,
                source_match."{_column_name("BHK")}" AS bhk,
                source_match."{_column_name("Buyer Budget")}" AS budget,
                source_match."{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE(source_match."{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE(source_match."{_column_name("Matched At")}", '') AS matched_at,
                COALESCE(NULLIF(counterparty."{_column_name("Last Seen")}", ''), NULLIF(counterparty."{_column_name("Date")}", ''), '') AS counterparty_recency
            FROM "{matches_table}" AS source_match
            LEFT JOIN "{_table_name("Structured Data")}" AS counterparty
                ON counterparty."{_column_name("Lead_ID")}" = source_match."{_column_name("Buyer Lead_ID")}"
            WHERE source_match."{_column_name("Seller Lead_ID")}" = ?
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER () AS row_count
            FROM normalized
            ORDER BY counterparty_recency DESC, match_score DESC, matched_at DESC, broker_name ASC
        )
        SELECT
            row_count,
            broker_name,
            broker_phone,
            property_type,
            location,
            bhk,
            budget,
            match_reason,
            match_score,
            matched_at
        FROM ranked
        LIMIT ? OFFSET ?
    '''
    with client._connect() as connection:
        rows = connection.execute(sql, [normalized_lead_id, normalized_lead_id, limit, offset]).fetchall()

    total = int(rows[0]["row_count"] or 0) if rows else 0
    payload_rows = []
    for row in rows:
        payload_rows.append(
            {
                "property_summary": " | ".join(
                    part
                    for part in [
                        _safe_str(row["property_type"]),
                        _safe_str(row["location"]),
                        f"{_safe_str(row['bhk'])} BHK" if _safe_str(row["bhk"]) else "",
                        f"Budget {_safe_str(row['budget'])}" if _safe_str(row["budget"]) else "",
                    ]
                    if part
                ),
                "broker_name": _safe_str(row["broker_name"]),
                "broker_phone": _safe_str(row["broker_phone"]),
                "match_reason": _safe_str(row["match_reason"]),
                "match_score": _safe_str(row["match_score"]),
                "matched_at": _safe_str(row["matched_at"]),
            }
        )
    return {"lead_id": normalized_lead_id, "row_count": total, "limit": limit, "offset": offset, "rows": payload_rows}


def _glide_sql_base_components(now: datetime, filter_config: dict[str, float]) -> dict[str, str]:
    structured_table = _table_name("Structured Data")
    execution_table = _table_name("Glide Execution")
    matches_table = _table_name("Matches")
    last_interaction_at = f'COALESCE(latest_execution."{_column_name("Last Interaction At")}", \'\')'
    last_seen_at = f'COALESCE(base_structured."{_column_name("Last Seen")}", \'\')'
    lead_date = f'COALESCE(base_structured."{_column_name("Date")}", \'\')'
    activity_source = (
        f'COALESCE(NULLIF({last_interaction_at}, \'\'), '
        f'NULLIF({last_seen_at}, \'\'), '
        f'NULLIF({lead_date}, \'\'), \'\')'
    )
    activity_date = f"SUBSTR({activity_source}, 1, 10)"
    priority_score = f'CAST(COALESCE(base_structured."{_column_name("Priority Score")}", \'0\') AS REAL)'
    follow_up_pending = f'UPPER(COALESCE(latest_execution."{_column_name("Follow-up Pending")}", \'\')) = \'TRUE\''
    activity_cutoff = (now.date() - timedelta(days=int(filter_config["activity_window_days"]))).isoformat()
    recent_cutoff = (now.date() - timedelta(days=int(filter_config["recent_interaction_days"]))).isoformat()
    priority_cutoff = float(filter_config["priority_qualified_score"])
    return {
        "structured_table": structured_table,
        "execution_table": execution_table,
        "matches_table": matches_table,
        "activity_date": activity_date,
        "activity_cutoff": activity_cutoff,
        "recent_cutoff": recent_cutoff,
        "priority_cutoff": str(priority_cutoff),
        "priority_score": priority_score,
        "follow_up_pending": follow_up_pending,
        "today_rule": (
            f"({activity_date} >= ? AND "
            f"({follow_up_pending} OR {priority_score} >= ? OR {activity_date} >= ?))"
        ),
        "active_rule": f"({activity_date} >= ?)",
        "recent_rule": f"({activity_date} >= ?)",
        "priority_rule": f"({priority_score} >= ?)",
        "priority_rank": (
            f"CASE "
            f"WHEN {priority_score} >= 80 THEN 0 "
            f"WHEN {priority_score} >= ? THEN 1 "
            f"WHEN {priority_score} > 0 THEN 2 "
            f"ELSE 3 END"
        ),
    }


def _compose_glide_item(
    row: dict[str, object],
    filter_config: dict[str, float],
    match_detail: dict[str, str] | None = None,
) -> dict[str, str]:
    detail = dict(_empty_match_detail())
    if match_detail:
        detail.update(match_detail)

    try:
        priority_score = float(row.get("priority_score") or 0)
    except Exception:
        priority_score = 0.0
    priority_cutoff = float(filter_config["priority_qualified_score"])
    priority = _priority_label_from_score(priority_score, priority_cutoff)
    match_count = _safe_str(row.get("match_count") or detail.get("match_count", "0")) or "0"
    display_property_type = _display_property_type(
        row.get("property_type"),
        row.get("bhk"),
        row.get("cleaned_message"),
        row.get("raw_message"),
    )

    item = {
        "lead_id": _safe_str(row.get("lead_id")),
        "name": _safe_str(row.get("name")),
        "phone": _safe_str(row.get("phone")),
        "lead_type": _safe_str(row.get("lead_type")),
        "entry_type": "Requirement" if _safe_str(row.get("lead_type")) == "Buyer" else "Property" if _safe_str(row.get("lead_type")) == "Seller" else "",
        "area": _safe_str(row.get("area")),
        "property_type": display_property_type,
        "bhk": _normalize_numeric(row.get("bhk")),
        "budget": _budget_text_from_values(
            {
                "Budget_Min": row.get("budget_min"),
                "Budget_Max": row.get("budget_max"),
                "Budget Range": row.get("budget_range"),
            }
        ),
        "transaction_type": _safe_str(row.get("transaction_type")),
        "priority": priority,
        "match_count": match_count,
        "strength": _strength_label_from_score(priority_score, priority_cutoff),
        "last_interaction_date": _normalize_date(row.get("last_interaction_at") or row.get("last_seen_at") or row.get("lead_date_raw")),
        "best_match_summary": detail.get("best_match_summary", ""),
        "match_reason": detail.get("match_reason", ""),
        "lead_date": _normalize_date(row.get("last_seen_at") or row.get("lead_date_raw")),
        "cleaned_message": _safe_str(row.get("cleaned_message")),
        "raw_message": _safe_str(row.get("raw_message")),
        "status": _safe_str(row.get("status")),
        "notes": _safe_str(row.get("notes")),
        "next_follow_up": _safe_str(row.get("next_follow_up")),
        "next_action": _safe_str(row.get("next_action")),
        "timing": _safe_str(row.get("timing")),
        "calc_active_within_120_days": _normalize_bool(_safe_str(row.get("calc_active_within_120_days")).upper() == "TRUE"),
        "calc_follow_up_pending": _normalize_bool(_safe_str(row.get("calc_follow_up_pending")).upper() == "TRUE"),
        "calc_priority_qualified": _normalize_bool(_safe_str(row.get("calc_priority_qualified")).upper() == "TRUE"),
        "calc_recent_interaction": _normalize_bool(_safe_str(row.get("calc_recent_interaction")).upper() == "TRUE"),
        "calc_in_today_view": _normalize_bool(_safe_str(row.get("calc_in_today_view")).upper() == "TRUE"),
        "last_seen_at": _safe_str(row.get("last_seen_at")),
        "updated_at": _safe_str(row.get("updated_at")) or _safe_str(row.get("last_seen_at")),
        "confidence_score": _normalize_numeric(row.get("confidence_score")),
        "extraction_flags": _safe_str(row.get("extraction_flags")),
        "first_seen_at": _safe_str(row.get("first_seen_at")),
        "repeat_count": _normalize_numeric(row.get("repeat_count")),
        "source": _safe_str(row.get("source")),
        "project_name": _safe_str(row.get("project_name")),
        "match_1_property_summary": detail.get("match_1_property_summary", ""),
        "match_1_broker_name": detail.get("match_1_broker_name", ""),
        "match_1_broker_phone": detail.get("match_1_broker_phone", ""),
        "match_2_property_summary": detail.get("match_2_property_summary", ""),
        "match_2_broker_name": detail.get("match_2_broker_name", ""),
        "match_2_broker_phone": detail.get("match_2_broker_phone", ""),
        "match_3_property_summary": detail.get("match_3_property_summary", ""),
        "match_3_broker_name": detail.get("match_3_broker_name", ""),
        "match_3_broker_phone": detail.get("match_3_broker_phone", ""),
    }
    return item


def _fetch_glide_base_rows_sql(
    client,
    *,
    mode: str,
    search: str,
    lead_type: str,
    property_type: str,
    from_date: date | None,
    to_date: date | None,
    limit: int,
    offset: int,
    now: datetime,
) -> dict[str, Any]:
    filter_config = get_glide_filter_config(client)
    parts = _glide_sql_base_components(now, filter_config)
    normalized_lead_type = _safe_str(lead_type).capitalize()
    normalized_property_type = _safe_str(property_type).lower()
    term = _safe_str(search).lower()
    like_term = f"%{term}%"

    where_parts = [
        f'COALESCE(base_structured."{_column_name("Lead_ID")}", \'\') <> \'\'',
        f'COALESCE(base_structured."{_column_name("Type")}", \'\') IN (\'Buyer\', \'Seller\')',
    ]
    params: list[object] = []

    if mode == "today":
        where_parts.append(parts["today_rule"])
        params.extend([parts["activity_cutoff"], float(parts["priority_cutoff"]), parts["recent_cutoff"]])
    if normalized_lead_type:
        where_parts.append(f'COALESCE(base_structured."{_column_name("Type")}", \'\') = ?')
        params.append(normalized_lead_type)
    if normalized_property_type:
        where_parts.append(
            "("
            + " OR ".join(
                [
                    f'LOWER(COALESCE(base_structured."{_column_name("Property Type")}", \'\')) LIKE ?',
                    f'LOWER(COALESCE(base_structured."{_column_name("BHK")}", \'\')) LIKE ?',
                ]
            )
            + ")"
        )
        params.extend([f"%{normalized_property_type}%", f"%{normalized_property_type}%"])
    if from_date:
        where_parts.append(f'{parts["activity_date"]} >= ?')
        params.append(from_date.isoformat())
    if to_date:
        where_parts.append(f'{parts["activity_date"]} <= ?')
        params.append(to_date.isoformat())
    if term:
        search_columns = [
            f'LOWER(COALESCE(base_structured."{_column_name("Name")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Contact Number")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Type")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Location")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Property Type")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("BHK")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Transaction Type")}", \'\')) LIKE ?',
            f'LOWER(COALESCE(base_structured."{_column_name("Project_Name")}", \'\')) LIKE ?',
            f'''EXISTS (
                SELECT 1
                FROM "{parts["matches_table"]}" AS match_search
                WHERE (
                    match_search."{_column_name("Buyer Lead_ID")}" = base_structured."{_column_name("Lead_ID")}"
                    AND LOWER(COALESCE(match_search."{_column_name("Seller Name")}", \'\')) LIKE ?
                ) OR (
                    match_search."{_column_name("Seller Lead_ID")}" = base_structured."{_column_name("Lead_ID")}"
                    AND LOWER(COALESCE(match_search."{_column_name("Buyer Name")}", \'\')) LIKE ?
                )
            )''',
        ]
        where_parts.append("(" + " OR ".join(search_columns) + ")")
        params.extend([like_term] * 10)

    where_sql = " AND ".join(where_parts)
    latest_execution_cte = f'''
        latest_execution AS (
            SELECT *
            FROM (
                SELECT
                    "{_column_name("Lead_ID")}" AS lead_id,
                    "{_column_name("Status")}" AS status,
                    "{_column_name("Notes")}" AS notes,
                    "{_column_name("Next Follow-up")}" AS next_follow_up,
                    "{_column_name("Next Action")}" AS next_action,
                    "{_column_name("Timing")}" AS timing,
                    "{_column_name("Last Interaction At")}" AS last_interaction_at,
                    "{_column_name("Follow-up Pending")}" AS follow_up_pending,
                    "{_column_name("Updated At")}" AS updated_at,
                    ROW_NUMBER() OVER (PARTITION BY "{_column_name("Lead_ID")}" ORDER BY row_id DESC) AS rn
                FROM "{parts["execution_table"]}"
                WHERE COALESCE("{_column_name("Lead_ID")}", '') <> ''
            ) ranked_execution
            WHERE rn = 1
        )
    '''
    match_counts_cte = f'''
        match_counts AS (
            SELECT lead_id, SUM(match_count) AS match_count
            FROM (
                SELECT "{_column_name("Buyer Lead_ID")}" AS lead_id, COUNT(*) AS match_count
                FROM "{parts["matches_table"]}"
                WHERE COALESCE("{_column_name("Buyer Lead_ID")}", '') <> ''
                GROUP BY "{_column_name("Buyer Lead_ID")}"
                UNION ALL
                SELECT "{_column_name("Seller Lead_ID")}" AS lead_id, COUNT(*) AS match_count
                FROM "{parts["matches_table"]}"
                WHERE COALESCE("{_column_name("Seller Lead_ID")}", '') <> ''
                GROUP BY "{_column_name("Seller Lead_ID")}"
            ) aggregated_matches
            GROUP BY lead_id
        )
    '''

    def build_base_cte(*, include_match_counts: bool) -> str:
        match_count_select = "COALESCE(match_counts.match_count, 0)" if include_match_counts else "0"
        match_count_join = (
            f'LEFT JOIN match_counts ON match_counts.lead_id = base_structured."{_column_name("Lead_ID")}"'
            if include_match_counts
            else ""
        )
        return f'''
        base_rows AS (
            SELECT
                base_structured."{_column_name("Lead_ID")}" AS lead_id,
                base_structured."{_column_name("Name")}" AS name,
                base_structured."{_column_name("Contact Number")}" AS phone,
                base_structured."{_column_name("Type")}" AS lead_type,
                base_structured."{_column_name("Location")}" AS area,
                base_structured."{_column_name("Property Type")}" AS property_type,
                base_structured."{_column_name("BHK")}" AS bhk,
                base_structured."{_column_name("Budget_Min")}" AS budget_min,
                base_structured."{_column_name("Budget_Max")}" AS budget_max,
                base_structured."{_column_name("Budget Range")}" AS budget_range,
                base_structured."{_column_name("Transaction Type")}" AS transaction_type,
                {parts["priority_score"]} AS priority_score,
                {match_count_select} AS match_count,
                base_structured."{_column_name("Date")}" AS lead_date_raw,
                base_structured."{_column_name("Last Seen")}" AS last_seen_at,
                base_structured."{_column_name("First Seen")}" AS first_seen_at,
                base_structured."{_column_name("Repeat Count")}" AS repeat_count,
                base_structured."{_column_name("Confidence Score")}" AS confidence_score,
                base_structured."{_column_name("Extraction Flags")}" AS extraction_flags,
                base_structured."{_column_name("Source")}" AS source,
                base_structured."{_column_name("Project_Name")}" AS project_name,
                base_structured."{_column_name("Cleaned Message")}" AS cleaned_message,
                base_structured."{_column_name("Raw Message")}" AS raw_message,
                COALESCE(latest_execution.status, '') AS status,
                COALESCE(latest_execution.notes, '') AS notes,
                COALESCE(latest_execution.next_follow_up, '') AS next_follow_up,
                COALESCE(latest_execution.next_action, '') AS next_action,
                COALESCE(latest_execution.timing, '') AS timing,
                COALESCE(latest_execution.last_interaction_at, '') AS last_interaction_at,
                COALESCE(latest_execution.follow_up_pending, '') AS calc_follow_up_pending,
                COALESCE(latest_execution.updated_at, '') AS updated_at,
                CASE WHEN {parts["active_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_active_within_120_days,
                CASE WHEN {parts["recent_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_recent_interaction,
                CASE WHEN {parts["priority_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_priority_qualified,
                CASE WHEN {parts["today_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_in_today_view,
                {parts["priority_rank"]} AS priority_rank
            FROM "{parts["structured_table"]}" AS base_structured
            LEFT JOIN latest_execution ON latest_execution.lead_id = base_structured."{_column_name("Lead_ID")}"
            {match_count_join}
            WHERE {where_sql}
        )
    '''

    base_params = [
        parts["activity_cutoff"],
        parts["recent_cutoff"],
        float(parts["priority_cutoff"]),
        parts["activity_cutoff"],
        float(parts["priority_cutoff"]),
        parts["recent_cutoff"],
        float(parts["priority_cutoff"]),
        *params,
    ]

    count_ctes = ",\n".join([latest_execution_cte, build_base_cte(include_match_counts=False)])
    count_sql = f"WITH {count_ctes} SELECT COUNT(*) AS row_count FROM base_rows"
    page_ctes = ",\n".join([latest_execution_cte, match_counts_cte, build_base_cte(include_match_counts=True)])
    page_sql = f'''
        WITH {page_ctes}
        SELECT *
        FROM base_rows
        ORDER BY COALESCE(NULLIF(last_seen_at, ''), NULLIF(lead_date_raw, ''), '') DESC, match_count DESC, priority_rank ASC, LOWER(name) ASC, lead_id DESC
        LIMIT ? OFFSET ?
    '''

    with client._connect() as connection:
        total = int(connection.execute(count_sql, base_params).fetchone()["row_count"] or 0)
        rows = connection.execute(page_sql, [*base_params, limit, offset]).fetchall()

    row_columns = [
        "lead_id",
        "name",
        "phone",
        "lead_type",
        "area",
        "property_type",
        "bhk",
        "budget_min",
        "budget_max",
        "budget_range",
        "transaction_type",
        "priority_score",
        "match_count",
        "lead_date_raw",
        "last_seen_at",
        "first_seen_at",
        "repeat_count",
        "confidence_score",
        "extraction_flags",
        "source",
        "project_name",
        "cleaned_message",
        "raw_message",
        "status",
        "notes",
        "next_follow_up",
        "next_action",
        "timing",
        "last_interaction_at",
        "calc_follow_up_pending",
        "updated_at",
        "calc_active_within_120_days",
        "calc_recent_interaction",
        "calc_priority_qualified",
        "calc_in_today_view",
        "priority_rank",
    ]
    row_payloads = [{column: row[column] for column in row_columns} for row in rows]
    match_details = _load_match_detail_for_leads(client, [row["lead_id"] for row in row_payloads]) if row_payloads else {}
    items = [
        _compose_glide_item(row, filter_config, match_details.get(_safe_str(row.get("lead_id")), {}))
        for row in row_payloads
    ]
    return {
        "mode": mode,
        "columns": list(GLIDE_VIEW_COLUMNS),
        "rows": items,
        "row_count": total,
        "page_size": limit,
        "offset": offset,
        "search": term,
        "lead_type": normalized_lead_type,
        "property_type": normalized_property_type,
    }


def _load_glide_lead_detail_sql(client, lead_id: str, *, now: datetime) -> dict[str, str] | None:
    normalized_lead_id = _safe_str(lead_id)
    if not normalized_lead_id:
        return None

    filter_config = get_glide_filter_config(client)
    parts = _glide_sql_base_components(now, filter_config)
    sql = f'''
        WITH
        latest_execution AS (
            SELECT *
            FROM (
                SELECT
                    "{_column_name("Lead_ID")}" AS lead_id,
                    "{_column_name("Status")}" AS status,
                    "{_column_name("Notes")}" AS notes,
                    "{_column_name("Next Follow-up")}" AS next_follow_up,
                    "{_column_name("Next Action")}" AS next_action,
                    "{_column_name("Timing")}" AS timing,
                    "{_column_name("Last Interaction At")}" AS last_interaction_at,
                    "{_column_name("Follow-up Pending")}" AS follow_up_pending,
                    "{_column_name("Updated At")}" AS updated_at,
                    ROW_NUMBER() OVER (PARTITION BY "{_column_name("Lead_ID")}" ORDER BY row_id DESC) AS rn
                FROM "{parts["execution_table"]}"
                WHERE COALESCE("{_column_name("Lead_ID")}", '') <> ''
            ) ranked_execution
            WHERE rn = 1
        ),
        match_counts AS (
            SELECT lead_id, SUM(match_count) AS match_count
            FROM (
                SELECT "{_column_name("Buyer Lead_ID")}" AS lead_id, COUNT(*) AS match_count
                FROM "{parts["matches_table"]}"
                WHERE "{_column_name("Buyer Lead_ID")}" = ?
                GROUP BY "{_column_name("Buyer Lead_ID")}"
                UNION ALL
                SELECT "{_column_name("Seller Lead_ID")}" AS lead_id, COUNT(*) AS match_count
                FROM "{parts["matches_table"]}"
                WHERE "{_column_name("Seller Lead_ID")}" = ?
                GROUP BY "{_column_name("Seller Lead_ID")}"
            ) aggregated_matches
            GROUP BY lead_id
        )
        SELECT
            base_structured."{_column_name("Lead_ID")}" AS lead_id,
            base_structured."{_column_name("Name")}" AS name,
            base_structured."{_column_name("Contact Number")}" AS phone,
            base_structured."{_column_name("Type")}" AS lead_type,
            base_structured."{_column_name("Location")}" AS area,
            base_structured."{_column_name("Property Type")}" AS property_type,
            base_structured."{_column_name("BHK")}" AS bhk,
            base_structured."{_column_name("Budget_Min")}" AS budget_min,
            base_structured."{_column_name("Budget_Max")}" AS budget_max,
            base_structured."{_column_name("Budget Range")}" AS budget_range,
            base_structured."{_column_name("Transaction Type")}" AS transaction_type,
            {parts["priority_score"]} AS priority_score,
            COALESCE(match_counts.match_count, 0) AS match_count,
            base_structured."{_column_name("Date")}" AS lead_date_raw,
            base_structured."{_column_name("Last Seen")}" AS last_seen_at,
            base_structured."{_column_name("First Seen")}" AS first_seen_at,
            base_structured."{_column_name("Repeat Count")}" AS repeat_count,
            base_structured."{_column_name("Confidence Score")}" AS confidence_score,
            base_structured."{_column_name("Extraction Flags")}" AS extraction_flags,
            base_structured."{_column_name("Source")}" AS source,
            base_structured."{_column_name("Project_Name")}" AS project_name,
            base_structured."{_column_name("Cleaned Message")}" AS cleaned_message,
            base_structured."{_column_name("Raw Message")}" AS raw_message,
            COALESCE(latest_execution.status, '') AS status,
            COALESCE(latest_execution.notes, '') AS notes,
            COALESCE(latest_execution.next_follow_up, '') AS next_follow_up,
            COALESCE(latest_execution.next_action, '') AS next_action,
            COALESCE(latest_execution.timing, '') AS timing,
            COALESCE(latest_execution.last_interaction_at, '') AS last_interaction_at,
            COALESCE(latest_execution.follow_up_pending, '') AS calc_follow_up_pending,
            COALESCE(latest_execution.updated_at, '') AS updated_at,
            CASE WHEN {parts["active_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_active_within_120_days,
            CASE WHEN {parts["recent_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_recent_interaction,
            CASE WHEN {parts["priority_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_priority_qualified,
            CASE WHEN {parts["today_rule"]} THEN 'TRUE' ELSE 'FALSE' END AS calc_in_today_view
        FROM "{parts["structured_table"]}" AS base_structured
        LEFT JOIN latest_execution ON latest_execution.lead_id = base_structured."{_column_name("Lead_ID")}"
        LEFT JOIN match_counts ON match_counts.lead_id = base_structured."{_column_name("Lead_ID")}"
        WHERE base_structured."{_column_name("Lead_ID")}" = ?
        LIMIT 1
    '''
    params = [
        normalized_lead_id,
        normalized_lead_id,
        parts["activity_cutoff"],
        parts["recent_cutoff"],
        float(parts["priority_cutoff"]),
        parts["activity_cutoff"],
        float(parts["priority_cutoff"]),
        parts["recent_cutoff"],
        normalized_lead_id,
    ]
    with client._connect() as connection:
        row = connection.execute(sql, params).fetchone()
    if row is None:
        return None
    payload_columns = [
        "lead_id",
        "name",
        "phone",
        "lead_type",
        "area",
        "property_type",
        "bhk",
        "budget_min",
        "budget_max",
        "budget_range",
        "transaction_type",
        "priority_score",
        "match_count",
        "lead_date_raw",
        "last_seen_at",
        "first_seen_at",
        "repeat_count",
        "confidence_score",
        "extraction_flags",
        "source",
        "project_name",
        "cleaned_message",
        "raw_message",
        "status",
        "notes",
        "next_follow_up",
        "next_action",
        "timing",
        "last_interaction_at",
        "calc_follow_up_pending",
        "updated_at",
        "calc_active_within_120_days",
        "calc_recent_interaction",
        "calc_priority_qualified",
        "calc_in_today_view",
    ]
    payload = {column: row[column] for column in payload_columns}
    return _compose_glide_item(payload, filter_config, _load_match_detail_for_lead(client, normalized_lead_id))


def _cached_glide_rows(client, *, now: datetime | None = None, force_refresh: bool = False) -> list[dict[str, str]]:
    now = now or datetime.now()
    if not hasattr(client, "_connect"):
        return build_glide_view(client, now=now)
    built_at = _GLIDE_CACHE["built_at"]
    cached_rows = _GLIDE_CACHE["rows"]
    client_key = _client_cache_key(client)
    if not force_refresh and built_at and cached_rows is not None and _GLIDE_CACHE.get("client_key") == client_key:
        age = (now - built_at).total_seconds()
        if age <= GLIDE_CACHE_TTL_SECONDS:
            return cached_rows

    with _GLIDE_CACHE_LOCK:
        built_at = _GLIDE_CACHE["built_at"]
        cached_rows = _GLIDE_CACHE["rows"]
        if not force_refresh and built_at and cached_rows is not None and _GLIDE_CACHE.get("client_key") == client_key:
            age = (now - built_at).total_seconds()
            if age <= GLIDE_CACHE_TTL_SECONDS:
                return cached_rows
        rows = build_glide_view(client, now=now)
        _GLIDE_CACHE["built_at"] = now
        _GLIDE_CACHE["rows"] = rows
        _GLIDE_CACHE["client_key"] = client_key
        return rows


def _search_blob(item: dict[str, str]) -> str:
    parts = [
        item.get("name", ""),
        item.get("phone", ""),
        item.get("lead_type", ""),
        item.get("entry_type", ""),
        item.get("area", ""),
        item.get("property_type", ""),
        item.get("bhk", ""),
        item.get("transaction_type", ""),
        item.get("project_name", ""),
        item.get("source", ""),
        item.get("match_1_broker_name", ""),
        item.get("match_2_broker_name", ""),
        item.get("match_3_broker_name", ""),
    ]
    return " ".join(parts).lower()


def _lead_in_today_view(item: dict[str, str], now: datetime, filter_config: dict[str, float]) -> bool:
    last_seen = _parse_datetime(item.get("last_seen_at"))
    active_within_window = bool(last_seen and last_seen.date() >= (now.date() - timedelta(days=int(filter_config["activity_window_days"]))))
    recent_interaction = bool(last_seen and last_seen.date() >= (now.date() - timedelta(days=int(filter_config["recent_interaction_days"]))))
    priority_qualified = item.get("calc_priority_qualified") == "TRUE"
    follow_up_pending = item.get("calc_follow_up_pending") == "TRUE"
    return active_within_window and (follow_up_pending or priority_qualified or recent_interaction)


def build_glide_view(client, *, now: datetime | None = None) -> list[dict[str, str]]:
    now = now or datetime.now()
    filter_config = get_glide_filter_config(client)
    execution_by_lead = client.get_glide_execution_map() if hasattr(client, "get_glide_execution_map") else {}
    matches_by_lead = _load_match_summary_by_lead(client)
    if not hasattr(client, "_connect"):
        leads = _load_structured_leads(client)

        items: list[dict[str, str]] = []
        for lead in leads:
            lead_id = _safe_str(lead.values.get("Lead_ID"))
            lead_type = _safe_str(lead.values.get("Type"))
            execution = execution_by_lead.get(lead_id, {})
            last_seen_at = _safe_str(lead.values.get("Last Seen"))
            last_seen_dt = _parse_datetime(last_seen_at)
            interaction_at = _safe_str(execution.get("Last Interaction At", ""))
            interaction_dt = _parse_datetime(interaction_at)
            activity_dt = interaction_dt or last_seen_dt
            active_within_120 = bool(activity_dt and activity_dt.date() >= (now.date() - timedelta(days=int(filter_config["activity_window_days"]))))
            recent_interaction = bool(activity_dt and activity_dt.date() >= (now.date() - timedelta(days=int(filter_config["recent_interaction_days"]))))
            priority_score = float(lead.values.get("Priority Score") or 0)
            priority_qualified = priority_score >= float(filter_config["priority_qualified_score"])
            follow_up_pending = _safe_str(execution.get("Follow-up Pending", "")).upper() == "TRUE"

            match_bucket = matches_by_lead.get(lead_id, {"match_count": 0, "slots": []})
            detail = _match_detail_from_bucket(match_bucket)
            item = {
                "lead_id": lead_id,
                "name": _safe_str(lead.values.get("Name")),
                "phone": _safe_str(lead.values.get("Contact Number")),
                "lead_type": lead_type,
                "entry_type": "Requirement" if lead_type == "Buyer" else "Property" if lead_type == "Seller" else "",
                "area": _safe_str(lead.values.get("Location")),
                "property_type": _display_property_type(
                    lead.values.get("Property Type"),
                    lead.values.get("BHK"),
                    lead.values.get("Cleaned Message"),
                    lead.values.get("Raw Message"),
                ),
                "bhk": _normalize_numeric(lead.values.get("BHK")),
                "budget": _budget_text(lead),
                "transaction_type": _safe_str(lead.values.get("Transaction Type")),
                "priority": _priority_badge(lead, float(filter_config["priority_qualified_score"])),
                "match_count": detail["match_count"],
                "strength": _strength_label(lead, float(filter_config["priority_qualified_score"])),
                "last_interaction_date": _normalize_date(interaction_at or last_seen_at or lead.values.get("Date")),
                "best_match_summary": detail["best_match_summary"],
                "match_reason": detail["match_reason"],
                "lead_date": _normalize_date(last_seen_at or lead.values.get("Date")),
                "cleaned_message": _safe_str(lead.values.get("Cleaned Message")),
                "raw_message": _safe_str(lead.values.get("Raw Message")),
                "status": _safe_str(execution.get("Status", "")),
                "notes": _safe_str(execution.get("Notes", "")),
                "next_follow_up": _safe_str(execution.get("Next Follow-up", "")),
                "next_action": _safe_str(execution.get("Next Action", "")),
                "timing": _safe_str(execution.get("Timing", "")),
                "calc_active_within_120_days": _normalize_bool(active_within_120),
                "calc_follow_up_pending": _normalize_bool(follow_up_pending),
                "calc_priority_qualified": _normalize_bool(priority_qualified),
                "calc_recent_interaction": _normalize_bool(recent_interaction),
                "calc_in_today_view": "FALSE",
                "last_seen_at": last_seen_at,
                "updated_at": _safe_str(execution.get("Updated At", "")) or last_seen_at,
                "confidence_score": _normalize_numeric(lead.values.get("Confidence Score")),
                "extraction_flags": _safe_str(lead.values.get("Extraction Flags")),
                "first_seen_at": _safe_str(lead.values.get("First Seen")),
                "repeat_count": _normalize_numeric(lead.values.get("Repeat Count")),
                "source": _safe_str(lead.values.get("Source")),
                "project_name": _safe_str(lead.values.get("Project_Name")),
                "match_1_property_summary": detail["match_1_property_summary"],
                "match_1_broker_name": detail["match_1_broker_name"],
                "match_1_broker_phone": detail["match_1_broker_phone"],
                "match_2_property_summary": detail["match_2_property_summary"],
                "match_2_broker_name": detail["match_2_broker_name"],
                "match_2_broker_phone": detail["match_2_broker_phone"],
                "match_3_property_summary": detail["match_3_property_summary"],
                "match_3_broker_name": detail["match_3_broker_name"],
                "match_3_broker_phone": detail["match_3_broker_phone"],
            }
            item["calc_in_today_view"] = _normalize_bool(_lead_in_today_view(item, now, filter_config))
            items.append(item)

        items.sort(key=_lead_sort_key)
        return items

    items: list[dict[str, str]] = []
    for lead_values in _load_glide_base_rows(client):
        lead_id = _safe_str(lead_values.get("Lead_ID"))
        lead_type = _safe_str(lead_values.get("Type"))
        execution = execution_by_lead.get(lead_id, {})
        last_seen_at = _safe_str(lead_values.get("Last Seen"))
        last_seen_dt = _parse_datetime(last_seen_at)
        interaction_at = _safe_str(execution.get("Last Interaction At", ""))
        interaction_dt = _parse_datetime(interaction_at)
        activity_dt = interaction_dt or last_seen_dt
        active_within_120 = bool(activity_dt and activity_dt.date() >= (now.date() - timedelta(days=int(filter_config["activity_window_days"]))))
        recent_interaction = bool(activity_dt and activity_dt.date() >= (now.date() - timedelta(days=int(filter_config["recent_interaction_days"]))))
        priority_score = float(lead_values.get("Priority Score") or 0)
        priority_qualified = priority_score >= float(filter_config["priority_qualified_score"])
        follow_up_pending = _safe_str(execution.get("Follow-up Pending", "")).upper() == "TRUE"

        detail = _match_detail_from_bucket(matches_by_lead.get(lead_id, {"match_count": 0, "slots": []}))
        item = {
            "lead_id": lead_id,
            "name": _safe_str(lead_values.get("Name")),
            "phone": _safe_str(lead_values.get("Contact Number")),
            "lead_type": lead_type,
            "entry_type": "Requirement" if lead_type == "Buyer" else "Property" if lead_type == "Seller" else "",
            "area": _safe_str(lead_values.get("Location")),
            "property_type": _display_property_type(
                lead_values.get("Property Type"),
                lead_values.get("BHK"),
                lead_values.get("Cleaned Message"),
                lead_values.get("Raw Message"),
            ),
            "bhk": _normalize_numeric(lead_values.get("BHK")),
            "budget": _budget_text_from_values(lead_values),
            "transaction_type": _safe_str(lead_values.get("Transaction Type")),
            "priority": _priority_badge(StructuredLead(lead_values), float(filter_config["priority_qualified_score"])),
            "match_count": detail["match_count"],
            "strength": _strength_label(StructuredLead(lead_values), float(filter_config["priority_qualified_score"])),
            "last_interaction_date": _normalize_date(interaction_at or last_seen_at or lead_values.get("Date")),
            "best_match_summary": detail["best_match_summary"],
            "match_reason": detail["match_reason"],
            "lead_date": _normalize_date(last_seen_at or lead_values.get("Date")),
            "cleaned_message": _safe_str(lead_values.get("Cleaned Message")),
            "raw_message": _safe_str(lead_values.get("Raw Message")),
            "status": _safe_str(execution.get("Status", "")),
            "notes": _safe_str(execution.get("Notes", "")),
            "next_follow_up": _safe_str(execution.get("Next Follow-up", "")),
            "next_action": _safe_str(execution.get("Next Action", "")),
            "timing": _safe_str(execution.get("Timing", "")),
            "calc_active_within_120_days": _normalize_bool(active_within_120),
            "calc_follow_up_pending": _normalize_bool(follow_up_pending),
            "calc_priority_qualified": _normalize_bool(priority_qualified),
            "calc_recent_interaction": _normalize_bool(recent_interaction),
            "calc_in_today_view": "FALSE",
            "last_seen_at": last_seen_at,
            "updated_at": _safe_str(execution.get("Updated At", "")) or last_seen_at,
            "confidence_score": _normalize_numeric(lead_values.get("Confidence Score")),
            "extraction_flags": _safe_str(lead_values.get("Extraction Flags")),
            "first_seen_at": _safe_str(lead_values.get("First Seen")),
            "repeat_count": _normalize_numeric(lead_values.get("Repeat Count")),
            "source": _safe_str(lead_values.get("Source")),
            "project_name": _safe_str(lead_values.get("Project_Name")),
            **detail,
        }
        item["calc_in_today_view"] = _normalize_bool(_lead_in_today_view(item, now, filter_config))
        items.append(item)

    items.sort(key=_lead_sort_key)
    return items


def _enrich_rows_with_match_counts(client, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    lead_ids = [row.get("lead_id", "") for row in rows]
    counts = _load_match_counts_for_leads(client, lead_ids)
    enriched: list[dict[str, str]] = []
    for row in rows:
        next_row = dict(row)
        next_row["match_count"] = str(counts.get(row.get("lead_id", ""), 0))
        enriched.append(next_row)
    enriched.sort(key=_lead_sort_key)
    return enriched


def get_glide_view_dataset(
    client,
    *,
    mode: str = "today",
    search: str = "",
    lead_type: str = "",
    property_type: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if mode not in {"today", "all"}:
        raise ValueError("mode must be 'today' or 'all'")
    normalized_lead_type = _safe_str(lead_type).capitalize()
    if normalized_lead_type and normalized_lead_type not in {"Buyer", "Seller"}:
        raise ValueError("lead_type must be 'Buyer', 'Seller', or empty")
    now = now or datetime.now()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    if hasattr(client, "_connect"):
        return _fetch_glide_base_rows_sql(
            client,
            mode=mode,
            search=search,
            lead_type=normalized_lead_type,
            property_type=property_type,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            offset=offset,
            now=now,
        )

    items = _cached_glide_rows(client, now=now)
    if mode == "today":
        items = [item for item in items if item["calc_in_today_view"] == "TRUE"]
    if normalized_lead_type:
        items = [item for item in items if item.get("lead_type", "") == normalized_lead_type]
    normalized_property_type = _safe_str(property_type).lower()
    if normalized_property_type:
        items = [
            item
            for item in items
            if normalized_property_type in _safe_str(item.get("property_type", "")).lower()
            or normalized_property_type in _safe_str(item.get("bhk", "")).lower()
        ]
    if from_date or to_date:
        filtered_items: list[dict[str, str]] = []
        for item in items:
            item_date = _normalize_date(item.get("lead_date") or item.get("last_seen_at") or item.get("last_interaction_date"))
            if not item_date:
                continue
            try:
                parsed_item_date = date.fromisoformat(item_date)
            except ValueError:
                continue
            if from_date and parsed_item_date < from_date:
                continue
            if to_date and parsed_item_date > to_date:
                continue
            filtered_items.append(item)
        items = filtered_items

    term = _safe_str(search).lower()
    if term:
        items = [item for item in items if term in _search_blob(item)]

    total = len(items)
    page = items[offset: offset + limit]
    return {
        "mode": mode,
        "columns": list(GLIDE_VIEW_COLUMNS),
        "rows": page,
        "row_count": total,
        "page_size": limit,
        "offset": offset,
        "search": term,
        "lead_type": normalized_lead_type,
        "property_type": normalized_property_type,
    }


def get_glide_lead_detail(client, lead_id: str, *, now: datetime | None = None) -> dict[str, str] | None:
    if lead_id.startswith(_STRUCTURED_ONLY_LEAD_PREFIX):
        return _load_structured_only_lead_detail(client, lead_id)
    if hasattr(client, "_connect"):
        return _load_glide_lead_detail_sql(client, lead_id, now=now or datetime.now())
    for item in _cached_glide_rows(client, now=now):
        if item["lead_id"] == lead_id:
            return item
    return None


def _load_structured_only_lead_detail(client, lead_id: str) -> dict[str, str] | None:
    if not hasattr(client, "_connect"):
        return None
    row_id = lead_id.removeprefix(_STRUCTURED_ONLY_LEAD_PREFIX).strip()
    if not row_id.isdigit():
        return None

    table_name = _table_name("Structured Data")
    with client._connect() as connection:
        row = connection.execute(f'SELECT * FROM "{table_name}" WHERE row_id = ?', [int(row_id)]).fetchone()
    if not row:
        return None

    values = dict(row)
    property_type = _display_property_type(
        values.get(_column_name("Property Type")),
        values.get(_column_name("BHK")),
        values.get(_column_name("Cleaned Message")),
        values.get(_column_name("Raw Message")),
    )
    return {
        "lead_id": lead_id,
        "name": _safe_str(values.get(_column_name("Name"))),
        "phone": _safe_str(values.get(_column_name("Contact Number"))),
        "lead_type": _safe_str(values.get(_column_name("Type"))),
        "entry_type": "Structured Only",
        "area": _safe_str(values.get(_column_name("Location"))),
        "property_type": property_type,
        "bhk": _normalize_numeric(values.get(_column_name("BHK"))),
        "budget": _safe_str(values.get(_column_name("Budget Range"))),
        "transaction_type": _safe_str(values.get(_column_name("Transaction Type"))),
        "priority": "SEARCH ONLY",
        "match_count": "",
        "strength": "Not available",
        "last_interaction_date": "-",
        "best_match_summary": "Structured-only search result.",
        "match_reason": "This row exists in Structured Data but is not a live Glide lead with match computation.",
        "lead_date": _normalize_date(values.get(_column_name("Last Seen")) or values.get(_column_name("Date"))),
        "cleaned_message": _safe_str(values.get(_column_name("Cleaned Message"))),
        "raw_message": _safe_str(values.get(_column_name("Raw Message"))),
        "status": "",
        "notes": "",
        "next_follow_up": "",
        "next_action": "",
        "timing": "",
        "calc_active_within_120_days": "FALSE",
        "calc_follow_up_pending": "FALSE",
        "calc_priority_qualified": "FALSE",
        "calc_recent_interaction": "FALSE",
        "calc_in_today_view": "FALSE",
        "last_seen_at": _safe_str(values.get(_column_name("Last Seen"))),
        "updated_at": _safe_str(values.get(_column_name("Last Seen"))),
        "confidence_score": _normalize_numeric(values.get(_column_name("Confidence Score"))),
        "extraction_flags": _safe_str(values.get(_column_name("Extraction Flags"))),
        "first_seen_at": _safe_str(values.get(_column_name("First Seen"))),
        "repeat_count": _normalize_numeric(values.get(_column_name("Repeat Count"))),
        "source": _safe_str(values.get(_column_name("Source"))),
        "project_name": _safe_str(values.get(_column_name("Project_Name"))),
        "match_1_property_summary": "",
        "match_1_broker_name": "",
        "match_1_broker_phone": "",
        "match_2_property_summary": "",
        "match_2_broker_name": "",
        "match_2_broker_phone": "",
        "match_3_property_summary": "",
        "match_3_broker_name": "",
        "match_3_broker_phone": "",
        "structured_only": "TRUE",
    }


def get_glide_readiness(client) -> dict[str, Any]:
    glide_rows = _cached_glide_rows(client)
    filter_config = get_glide_filter_config(client)
    structured_columns = set(STRUCTURED_COLUMNS)
    requirements = [
        {
            "key": "glide_tab",
            "label": "Glide builder tab is available in the app",
            "status": "ready",
            "notes": "Implemented as an internal builder/preview tab over DB data.",
        },
        {
            "key": "db_source_of_truth",
            "label": "Database remains the single source of truth",
            "status": "ready",
            "notes": "Glide preview reads from DB-derived Glide_View only.",
        },
        {
            "key": "glide_view_projection",
            "label": "Deterministic Glide_View projection is available",
            "status": "ready",
            "notes": f"{len(glide_rows)} lead rows can be projected from Structured Data and Matches.",
        },
        {
            "key": "fixed_match_slots",
            "label": "Exactly 3 fixed match slots are exposed per lead",
            "status": "ready",
            "notes": "Frontend renders three fixed blocks only, backed by precomputed slot fields.",
        },
        {
            "key": "today_filter_backend",
            "label": "Today inclusion rule is enforced in backend",
            "status": "ready",
            "notes": f"Uses DB-controlled activity window ({int(filter_config['activity_window_days'])}d), recent interaction ({int(filter_config['recent_interaction_days'])}d), priority threshold ({int(filter_config['priority_qualified_score'])}), and DB-backed follow-up pending signals.",
        },
        {
            "key": "execution_fields",
            "label": "Execution fields (status, notes, next follow-up) exist for Glide",
            "status": "ready",
            "notes": "Fields are stored in DB-backed Glide Execution records and merged into Glide_View.",
        },
        {
            "key": "sheet_sync_logging",
            "label": "Write logging and sync error tracking are implemented",
            "status": "ready",
            "notes": "Execution edits and explicit actions write Glide log rows and set sync error flags on mismatch.",
        },
        {
            "key": "cta_and_alerts",
            "label": "CTA, alerts capture, and website contact flow are implemented",
            "status": "partial",
            "notes": "DB-backed Alerts Leads capture is implemented in the Glide tab; broader website CTA flow is still not complete.",
        },
        {
            "key": "deals_log",
            "label": "Deals Log high-signal memory is implemented",
            "status": "ready",
            "notes": "DB-backed Deals Log is available as a manual append-only Glide support surface.",
        },
        {
            "key": "system_errors_sheet",
            "label": "System_Errors logging exists",
            "status": "missing",
            "notes": "Current phase surfaces API errors but does not persist a System_Errors table.",
        },
    ]

    field_ownership = {
        "computed_read_only": [
            "calc_active_within_120_days",
            "calc_follow_up_pending",
            "calc_priority_qualified",
            "calc_recent_interaction",
            "calc_in_today_view",
            "match_count",
            "best_match_summary",
            "match_reason",
            "match_1_*",
            "match_2_*",
            "match_3_*",
        ],
        "future_execution_fields": ["status", "notes", "next_follow_up"],
    }
    sync_rules = [
        "DB is the source of truth.",
        "Glide consumes only the DB-derived Glide_View projection.",
        "No hidden logic or silent fallback in the frontend.",
        "All match data is precomputed before rendering.",
        "Today inclusion is backend-only and controlled via DB config, not per-lead manual overrides.",
    ]
    return {
        "overview": {
            "glide_view_rows": len(glide_rows),
            "structured_columns_present": len(structured_columns),
            "missing_execution_fields": [],
        },
        "filter_config": filter_config,
        "requirements": requirements,
        "field_ownership": field_ownership,
        "sync_rules": sync_rules,
    }
