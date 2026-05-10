from __future__ import annotations

from threading import Lock
from datetime import date, datetime, timedelta
from typing import Any

from .db_client import DEFAULT_CONFIG, REQUIRED_TABS, _column_name, _table_name
from .schemas import MATCH_COLUMNS, STRUCTURED_COLUMNS, StructuredLead

GLIDE_CACHE_TTL_SECONDS = 300

GLIDE_VIEW_COLUMNS = [
    "lead_id",
    "name",
    "phone",
    "area",
    "bhk",
    "budget",
    "transaction_type",
    "priority",
    "match_count",
    "strength",
    "last_interaction_date",
    "best_match_summary",
    "match_reason",
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

_GLIDE_CACHE: dict[str, Any] = {"built_at": None, "rows": None}
_GLIDE_CACHE_LOCK = Lock()


def invalidate_glide_cache() -> None:
    with _GLIDE_CACHE_LOCK:
        _GLIDE_CACHE["built_at"] = None
        _GLIDE_CACHE["rows"] = None


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
        "Location",
        "BHK",
        "Budget_Min",
        "Budget_Max",
        "Budget Range",
        "Type",
        "Priority Score",
        "Date",
        "Last Seen",
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


def _load_match_summary_from_rows(match_rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    lead_map: dict[str, dict[str, Any]] = {}
    for row in match_rows:
        for lead_key, broker_name_key, broker_phone_key, budget_key in (
            ("Buyer Lead_ID", "Seller Name", "Seller Phone", "Seller Budget"),
            ("Seller Lead_ID", "Buyer Name", "Buyer Phone", "Buyer Budget"),
        ):
            lead_id = _safe_str(row.get(lead_key, ""))
            if not lead_id:
                continue
            bucket = lead_map.setdefault(lead_id, {"match_count": 0, "slots": []})
            bucket["match_count"] += 1
            if len(bucket["slots"]) < 3:
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
                    }
                )
    return lead_map


def _load_match_summary_by_lead(client) -> dict[str, dict[str, Any]]:
    matches_table = _table_name("Matches")
    lead_map: dict[str, dict[str, Any]] = {}
    if not hasattr(client, "_connect"):
        dataset = client.get_table_rows("Matches")
        rows = dataset.get("rows", [])
        normalized_rows = [{column: _safe_str(row.get(column, "")) for column in MATCH_COLUMNS} for row in rows]
        return _load_match_summary_from_rows(normalized_rows)
    sql = f'''
        WITH normalized AS (
            SELECT
                "{_column_name("Buyer Lead_ID")}" AS lead_id,
                "{_column_name("Seller Name")}" AS broker_name,
                "{_column_name("Seller Phone")}" AS broker_phone,
                "{_column_name("Property Type")}" AS property_type,
                "{_column_name("Location")}" AS location,
                "{_column_name("BHK")}" AS bhk,
                "{_column_name("Seller Budget")}" AS budget,
                "{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE("{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE("{_column_name("Matched At")}", '') AS matched_at
            FROM "{matches_table}"
            WHERE COALESCE("{_column_name("Buyer Lead_ID")}", '') <> ''
            UNION ALL
            SELECT
                "{_column_name("Seller Lead_ID")}" AS lead_id,
                "{_column_name("Buyer Name")}" AS broker_name,
                "{_column_name("Buyer Phone")}" AS broker_phone,
                "{_column_name("Property Type")}" AS property_type,
                "{_column_name("Location")}" AS location,
                "{_column_name("BHK")}" AS bhk,
                "{_column_name("Buyer Budget")}" AS budget,
                "{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE("{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE("{_column_name("Matched At")}", '') AS matched_at
            FROM "{matches_table}"
            WHERE COALESCE("{_column_name("Seller Lead_ID")}", '') <> ''
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER (PARTITION BY lead_id) AS match_count,
                ROW_NUMBER() OVER (PARTITION BY lead_id ORDER BY match_score DESC, matched_at DESC, broker_name ASC) AS rn
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


def _load_match_detail_for_lead(client, lead_id: str) -> dict[str, str]:
    normalized_lead_id = lead_id.strip()
    if not normalized_lead_id:
        return _empty_match_detail()

    if not hasattr(client, "_connect"):
        return _match_detail_from_bucket(_load_match_summary_by_lead(client).get(normalized_lead_id, {}))

    matches_table = _table_name("Matches")
    sql = f'''
        WITH normalized AS (
            SELECT
                "{_column_name("Buyer Lead_ID")}" AS lead_id,
                "{_column_name("Seller Name")}" AS broker_name,
                "{_column_name("Seller Phone")}" AS broker_phone,
                "{_column_name("Property Type")}" AS property_type,
                "{_column_name("Location")}" AS location,
                "{_column_name("BHK")}" AS bhk,
                "{_column_name("Seller Budget")}" AS budget,
                "{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE("{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE("{_column_name("Matched At")}", '') AS matched_at
            FROM "{matches_table}"
            WHERE "{_column_name("Buyer Lead_ID")}" = ?
            UNION ALL
            SELECT
                "{_column_name("Seller Lead_ID")}" AS lead_id,
                "{_column_name("Buyer Name")}" AS broker_name,
                "{_column_name("Buyer Phone")}" AS broker_phone,
                "{_column_name("Property Type")}" AS property_type,
                "{_column_name("Location")}" AS location,
                "{_column_name("BHK")}" AS bhk,
                "{_column_name("Buyer Budget")}" AS budget,
                "{_column_name("Match Reason")}" AS match_reason,
                CAST(COALESCE("{_column_name("Match Score")}", '0') AS REAL) AS match_score,
                COALESCE("{_column_name("Matched At")}", '') AS matched_at
            FROM "{matches_table}"
            WHERE "{_column_name("Seller Lead_ID")}" = ?
        ),
        ranked AS (
            SELECT
                *,
                COUNT(*) OVER () AS match_count,
                ROW_NUMBER() OVER (ORDER BY match_score DESC, matched_at DESC, broker_name ASC) AS rn
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
            rn
        FROM ranked
        WHERE rn <= 3
        ORDER BY rn
    '''
    with client._connect() as connection:
        rows = connection.execute(sql, [normalized_lead_id, normalized_lead_id]).fetchall()

    bucket = {"match_count": 0, "slots": []}
    for row in rows:
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
            }
        )
    return _match_detail_from_bucket(bucket)


def _cached_glide_rows(client, *, now: datetime | None = None, force_refresh: bool = False) -> list[dict[str, str]]:
    now = now or datetime.now()
    built_at = _GLIDE_CACHE["built_at"]
    cached_rows = _GLIDE_CACHE["rows"]
    if not force_refresh and built_at and cached_rows is not None:
        age = (now - built_at).total_seconds()
        if age <= GLIDE_CACHE_TTL_SECONDS:
            return cached_rows

    with _GLIDE_CACHE_LOCK:
        built_at = _GLIDE_CACHE["built_at"]
        cached_rows = _GLIDE_CACHE["rows"]
        if not force_refresh and built_at and cached_rows is not None:
            age = (now - built_at).total_seconds()
            if age <= GLIDE_CACHE_TTL_SECONDS:
                return cached_rows
        rows = build_glide_view(client, now=now)
        _GLIDE_CACHE["built_at"] = now
        _GLIDE_CACHE["rows"] = rows
        return rows


def _search_blob(item: dict[str, str]) -> str:
    parts = [
        item.get("name", ""),
        item.get("area", ""),
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
    if not hasattr(client, "_connect"):
        leads = _load_structured_leads(client)
        matches_by_lead = _load_match_summary_by_lead(client)

        items: list[dict[str, str]] = []
        for lead in leads:
            lead_id = _safe_str(lead.values.get("Lead_ID"))
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
                "area": _safe_str(lead.values.get("Location")),
                "bhk": _normalize_numeric(lead.values.get("BHK")),
                "budget": _budget_text(lead),
                "transaction_type": _safe_str(lead.values.get("Type")),
                "priority": _priority_badge(lead, float(filter_config["priority_qualified_score"])),
                "match_count": detail["match_count"],
                "strength": _strength_label(lead, float(filter_config["priority_qualified_score"])),
                "last_interaction_date": _normalize_date(interaction_at or last_seen_at or lead.values.get("Date")),
                "best_match_summary": detail["best_match_summary"],
                "match_reason": detail["match_reason"],
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

        items.sort(
            key=lambda entry: (
                -float(entry.get("match_count") or 0),
                entry.get("priority", ""),
                entry.get("name", "").lower(),
            )
        )
        return items

    items: list[dict[str, str]] = []
    for lead_values in _load_glide_base_rows(client):
        lead_id = _safe_str(lead_values.get("Lead_ID"))
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

        item = {
            "lead_id": lead_id,
            "name": _safe_str(lead_values.get("Name")),
            "phone": _safe_str(lead_values.get("Contact Number")),
            "area": _safe_str(lead_values.get("Location")),
            "bhk": _normalize_numeric(lead_values.get("BHK")),
            "budget": _budget_text_from_values(lead_values),
            "transaction_type": _safe_str(lead_values.get("Type")),
            "priority": _priority_badge(StructuredLead(lead_values), float(filter_config["priority_qualified_score"])),
            "match_count": "0",
            "strength": _strength_label(StructuredLead(lead_values), float(filter_config["priority_qualified_score"])),
            "last_interaction_date": _normalize_date(interaction_at or last_seen_at or lead_values.get("Date")),
            "best_match_summary": "",
            "match_reason": "",
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
            **_empty_match_detail(),
        }
        item["calc_in_today_view"] = _normalize_bool(_lead_in_today_view(item, now, filter_config))
        items.append(item)

    items.sort(
        key=lambda entry: (
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNRANKED": 3}.get(entry.get("priority", ""), 4),
            -float(entry.get("bhk") or 0),
            entry.get("name", "").lower(),
        )
    )
    return items


def _enrich_rows_with_match_counts(client, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    lead_ids = [row.get("lead_id", "") for row in rows]
    counts = _load_match_counts_for_leads(client, lead_ids)
    enriched: list[dict[str, str]] = []
    for row in rows:
        next_row = dict(row)
        next_row["match_count"] = str(counts.get(row.get("lead_id", ""), 0))
        enriched.append(next_row)
    enriched.sort(
        key=lambda entry: (
            -float(entry.get("match_count") or 0),
            entry.get("priority", ""),
            entry.get("name", "").lower(),
        )
    )
    return enriched


def get_glide_view_dataset(
    client,
    *,
    mode: str = "today",
    search: str = "",
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    if mode not in {"today", "all"}:
        raise ValueError("mode must be 'today' or 'all'")

    items = _cached_glide_rows(client, now=now)
    if mode == "today":
        items = [item for item in items if item["calc_in_today_view"] == "TRUE"]

    term = _safe_str(search).lower()
    if term:
        items = [item for item in items if term in _search_blob(item)]

    total = len(items)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    page = items[offset: offset + limit]
    if hasattr(client, "_connect"):
        page = _enrich_rows_with_match_counts(client, page)
    return {
        "mode": mode,
        "columns": list(GLIDE_VIEW_COLUMNS),
        "rows": page,
        "row_count": total,
        "page_size": limit,
        "offset": offset,
        "search": term,
    }


def get_glide_lead_detail(client, lead_id: str, *, now: datetime | None = None) -> dict[str, str] | None:
    for item in _cached_glide_rows(client, now=now):
        if item["lead_id"] == lead_id:
            if not hasattr(client, "_connect"):
                return item
            detail = dict(item)
            detail.update(_load_match_detail_for_lead(client, lead_id))
            return detail
    return None


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
