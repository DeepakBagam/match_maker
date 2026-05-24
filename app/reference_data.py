from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .config import (
    REFERENCE_HIGH_CONFIDENCE_SCORE,
    RETENTION_RULES,
    SPECIAL_ASSET_TYPES,
)
from .schemas import StructuredLead

REFERENCE_DATA_COLUMNS = [
    "Lead_ID",
    "Name",
    "Phone",
    "Entry_Type",
    "Lead_Type",
    "Location",
    "Property_Type",
    "Budget",
    "BHK",
    "Society",
    "Landmark",
    "Last_Seen",
    "Created_Date",
    "Source",
    "Broker",
    "data_status",
    "Confidence_Score",
    "retention_period",
    "retention_until",
]

_LANDMARK_PATTERNS = (
    r"\bnear\s+([a-z0-9][a-z0-9\s\-]{2,40})",
    r"\bopposite\s+([a-z0-9][a-z0-9\s\-]{2,40})",
    r"\bopp\.?\s+([a-z0-9][a-z0-9\s\-]{2,40})",
    r"\bbeside\s+([a-z0-9][a-z0-9\s\-]{2,40})",
    r"\bbehind\s+([a-z0-9][a-z0-9\s\-]{2,40})",
)
_BROKER_CONTACT_RE = re.compile(r"([a-z][a-z\s.'&/-]{1,40})\s+(?:\+?91[-\s]?)?\d{10}\b", re.IGNORECASE)
_BROKER_COMPANY_RE = re.compile(
    r"([a-z][a-z0-9\s.'&/-]{2,60}(?:realty|real estate|properties|property|estate|associates|group|realtor|brokers?))",
    re.IGNORECASE,
)
_BROKER_IGNORE_RE = re.compile(r"\b(?:call to view|to view|please dm|call|contact|reach|whatsapp|wa|rent|sale)\b", re.IGNORECASE)
_BROKER_CONTACT_NOISE_RE = re.compile(r"\b(?:car|park|price|lakh|lac|cr|crore|sqft|flat|office|shop|showroom|plot|villa|bungalow)\b", re.IGNORECASE)


def _safe_str(value: object) -> str:
    return "" if value is None else str(value).strip()


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


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


def _entry_type_for_lead(lead_type: str) -> str:
    normalized = lead_type.strip().lower()
    if normalized == "seller":
        return "Property"
    if normalized == "buyer":
        return "Requirement"
    return ""


def _budget_text(lead: StructuredLead) -> str:
    minimum = _safe_str(lead.values.get("Budget_Min"))
    maximum = _safe_str(lead.values.get("Budget_Max"))
    budget_range = _safe_str(lead.values.get("Budget Range"))
    if minimum and maximum:
        return minimum if minimum == maximum else f"{minimum}-{maximum}"
    return budget_range


def _normalize_bhk(value: object) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    try:
        number = float(raw)
    except ValueError:
        return raw
    return str(int(number)) if number.is_integer() else raw


def _normalize_broker_name(value: object) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    cleaned = re.sub(r"^[~@#\-\s]+", "", raw)
    cleaned = re.sub(r"^(?:call|contact|reach|whatsapp|wa)\s+",
                     "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
    return cleaned


def _title_preserve_short_tokens(value: str) -> str:
    words = []
    for token in re.split(r"(\s+|/)", value):
        if not token or token.isspace() or token == "/":
            words.append(token)
            continue
        words.append(token if token.isupper() and len(token) <= 4 else token.title())
    return "".join(words).strip(" ,.-/")


def _extract_broker_names(*values: object) -> str:
    raw_candidates: list[str] = []
    for index, value in enumerate(values):
        text = _safe_str(value)
        if not text:
            continue
        lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        for line in lines:
            if any(ch.isdigit() for ch in line):
                for match in _BROKER_CONTACT_RE.finditer(line):
                    candidate = re.sub(r"\s+", " ", match.group(1)).strip(" *|,.-")
                    if (
                        candidate
                        and len(candidate.split()) <= 3
                        and not _BROKER_IGNORE_RE.search(candidate)
                        and not _BROKER_CONTACT_NOISE_RE.search(candidate)
                    ):
                        raw_candidates.append(candidate)
                continue
            if index == 0:
                for match in _BROKER_COMPANY_RE.finditer(line):
                    candidate = re.sub(r"\s+", " ", match.group(1)).strip(" *|,.-")
                    if candidate and not _BROKER_IGNORE_RE.search(candidate):
                        raw_candidates.append(candidate)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        cleaned = _title_preserve_short_tokens(candidate.replace("’", "'"))
        key = cleaned.lower()
        if not cleaned or key in seen or cleaned.lower() == "to view":
            continue
        seen.add(key)
        normalized.append(cleaned)
    return " | ".join(normalized)


def _extract_landmark(*values: object) -> str:
    text = " ".join(_safe_str(value) for value in values if _safe_str(value))
    if not text:
        return ""
    lowered = text.lower()
    for pattern in _LANDMARK_PATTERNS:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" ,.-")
        if candidate:
            return candidate.title()
    return ""


def _is_special_asset(property_type: str) -> bool:
    normalized = property_type.strip().lower()
    if not normalized:
        return False
    return any(token.lower() in normalized for token in SPECIAL_ASSET_TYPES)


def _retention_period_days(property_type: str) -> int:
    if _is_special_asset(property_type):
        return int(RETENTION_RULES["special"])
    return int(RETENTION_RULES["default"])


def _retention_until(last_seen_at: datetime | None, property_type: str) -> date | None:
    if last_seen_at is None:
        return None
    return last_seen_at.date() + timedelta(days=_retention_period_days(property_type))


def _eligible_for_reference(lead: StructuredLead) -> bool:
    lead_type = _safe_str(lead.values.get("Type"))
    if lead_type not in {"Buyer", "Seller"}:
        return False
    if not _safe_str(lead.values.get("Contact Number")):
        return False

    data_status = _safe_str(lead.values.get("data_status")).upper()
    confidence = _safe_float(lead.values.get("Confidence Score"))
    if data_status == "APPROVED":
        return True
    return confidence >= REFERENCE_HIGH_CONFIDENCE_SCORE


def build_reference_rows(
    leads: list[StructuredLead],
    *,
    today: date | None = None,
) -> list[list[object]]:
    today = today or datetime.now().date()
    rows: list[list[object]] = []

    for lead in leads:
        if not _eligible_for_reference(lead):
            continue

        lead_type = _safe_str(lead.values.get("Type"))
        property_type = _safe_str(lead.values.get("Property Type"))
        last_seen_at = _parse_datetime(lead.values.get("Last Seen")) or _parse_datetime(lead.values.get("First Seen"))
        retention_until = _retention_until(last_seen_at, property_type)
        if retention_until and retention_until < today:
            continue

        created_at = _parse_datetime(lead.values.get("First Seen")) or _parse_datetime(lead.values.get("Date"))
        created_date = created_at.date().isoformat() if created_at else _safe_str(lead.values.get("Date"))
        society = _safe_str(lead.values.get("Project_Name"))
        landmark = _extract_landmark(lead.values.get("Raw Message"), lead.values.get("Cleaned Message"))
        confidence_score = _safe_str(lead.values.get("Confidence Score"))
        retention_period = _retention_period_days(property_type)

        rows.append(
            [
                _safe_str(lead.values.get("Lead_ID")),
                _safe_str(lead.values.get("Name")),
                _safe_str(lead.values.get("Contact Number")),
                _entry_type_for_lead(lead_type),
                lead_type,
                _safe_str(lead.values.get("Location")),
                property_type,
                _budget_text(lead),
                _normalize_bhk(lead.values.get("BHK")),
                society,
                landmark,
                last_seen_at.isoformat(sep=" ", timespec="seconds") if last_seen_at else "",
                created_date,
                _safe_str(lead.values.get("Source")),
                _extract_broker_names(
                    lead.values.get("Raw Message"),
                    lead.values.get("Cleaned Message"),
                    lead.values.get("Name"),
                ) or _normalize_broker_name(lead.values.get("Name")),
                _safe_str(lead.values.get("data_status")).upper() or "RAW",
                confidence_score,
                str(retention_period),
                retention_until.isoformat() if retention_until else "",
            ]
        )

    rows.sort(key=lambda row: (row[11], row[0]), reverse=True)
    return rows
