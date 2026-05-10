from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


STRUCTURED_COLUMNS = [
    "Date",
    "Month",
    "Week",
    "Time",
    "Source",
    "Type",
    "Transaction Type",
    "Location",
    "Property Type",
    "BHK",
    "Budget Range",
    "Budget_Min",
    "Budget_Max",
    "Area_Sqft",
    "Furnishing",
    "Project_Name",
    "Contact Number",
    "Name",
    "Raw Message",
    "Cleaned Message",
    "Lead Summary",
    "Extraction Status",
    "Confidence Score",
    "location_confidence",
    "budget_confidence",
    "bhk_confidence",
    "Extraction Flags",
    "Lead_ID",
    "Contact_ID",
    "Repeat Count",
    "Incomplete Data",
    "data_status",
    "First Seen",
    "Last Seen",
    "Priority Score",
    "Priority Reason",
]

RAW_COLUMNS = ["Ingested At", "Source", "Sender", "Timestamp", "Raw Message"]

MANUAL_COLUMNS = [
    "Submitted At",
    "Name",
    "Phone",
    "Requirement",
    "Location",
    "Budget",
    "Notes",
    "Source",
]

MATCH_COLUMNS = [
    "Date",
    "Month",
    "Week",
    "Buyer Lead_ID",
    "Buyer Name",
    "Buyer Phone",
    "Seller Lead_ID",
    "Seller Name",
    "Seller Phone",
    "Location",
    "Property Type",
    "BHK",
    "Buyer Budget",
    "Seller Budget",
    "Match Score",
    "Match Reason",
    "Matched At",
]

CLEAN_DATA_COLUMNS = [
    "Date",
    "Month",
    "Week",
    "Type",
    "Transaction Type",
    "Location",
    "Property Type",
    "BHK",
    "Budget Range",
]

TOP_LEAD_COLUMNS = [
    "Lead_ID",
    "Name",
    "Contact Number",
    "Type",
    "Transaction Type",
    "Location",
    "Property Type",
    "BHK",
    "Budget_Min",
    "Budget_Max",
    "Priority Score",
    "Priority Reason",
    "Confidence Score",
    "Last Seen",
    "Source",
]

SUMMARY_DEMAND_COLUMNS = ["Location", "BHK", "Budget_Min", "Budget_Max", "Count"]
SUMMARY_SUPPLY_COLUMNS = ["Location", "Property Type", "BHK", "Price_Min", "Price_Max", "Count"]
MATCH_REVIEW_COLUMNS = MATCH_COLUMNS
TOP_LEAD_REVIEW_COLUMNS = TOP_LEAD_COLUMNS
FINAL_VALIDATION_COLUMNS = ["Check", "Value", "Status", "Notes"]
VALIDATION_COLUMNS = [
    "Date",
    "Time",
    "Source",
    "Name",
    "Contact Number",
    "Type",
    "Transaction Type",
    "Location",
    "Property Type",
    "BHK",
    "Budget_Min",
    "Budget_Max",
    "Extraction Status",
    "Extraction Flags",
    "Lead Summary",
    "Cleaned Message",
    "Raw Message",
    "First Seen",
    "Last Seen",
]

INTEGER_COLUMNS = {"BHK", "Budget_Min", "Budget_Max", "Repeat Count", "Area_Sqft"}
FLOAT_COLUMNS = {
    "Confidence Score",
    "location_confidence",
    "budget_confidence",
    "bhk_confidence",
    "Priority Score",
}


def _coerce_value(column: str, value: Any) -> Any:
    if value in ("", None):
        return None if column in INTEGER_COLUMNS or column in FLOAT_COLUMNS else ""
    if column in INTEGER_COLUMNS:
        try:
            return int(float(value))
        except Exception:
            return None
    if column in FLOAT_COLUMNS:
        try:
            return float(value)
        except Exception:
            return None
    return value


@dataclass
class ParsedMessage:
    timestamp: datetime
    sender: str
    message: str
    source: str
    raw_message: str = ""


@dataclass
class StructuredLead:
    values: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> list[Any]:
        row: list[Any] = []
        for col in STRUCTURED_COLUMNS:
            value = _coerce_value(col, self.values.get(col, ""))
            row.append("" if value is None else value)
        return row

    @classmethod
    def from_row(cls, row: list[Any]) -> "StructuredLead":
        payload = {
            col: _coerce_value(col, row[idx] if idx < len(row) else "")
            for idx, col in enumerate(STRUCTURED_COLUMNS)
        }
        return cls(payload)
