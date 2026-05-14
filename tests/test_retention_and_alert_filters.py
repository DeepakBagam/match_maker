from datetime import date, datetime

from app.db_client import DatabaseClient
from app.glide_builder import get_glide_view_dataset
from app.pipeline import _effective_lookback_days, _prune_old_tracking_data
from app.schemas import StructuredLead


def _client(tmp_path) -> DatabaseClient:
    return DatabaseClient(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


def test_effective_lookback_defaults_to_one_year():
    assert _effective_lookback_days(None) == 365
    assert _effective_lookback_days(0) == 365
    assert _effective_lookback_days("") == 365
    assert _effective_lookback_days(30) == 30


def test_alerts_leads_supports_date_range_filtering(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Alerts Leads",
        [
            ["2026-05-13 09:00:00", "old@example.com", "Website"],
            ["2026-05-14 10:30:00", "today@example.com", "Website"],
            ["2026-05-15 08:45:00", "future@example.com", "Website"],
        ],
    )

    payload = client.get_table_page(
        "Alerts Leads",
        from_date="2026-05-14",
        to_date="2026-05-14",
    )

    assert payload["row_count"] == 1
    assert payload["rows"][0]["Contact"] == "today@example.com"


def test_prune_old_tracking_data_removes_rows_older_than_retention(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            StructuredLead({"Lead_ID": "old", "Date": "2025-05-12"}).to_row(),
            StructuredLead({"Lead_ID": "keep", "Date": "2025-05-14"}).to_row(),
        ],
    )
    client.append_rows(
        "Raw Data",
        [
            ["2026-05-14 12:00:00", "test", "sender", "2025-05-12 09:00:00", "old raw"],
            ["2026-05-14 12:00:00", "test", "sender", "2025-05-14 09:00:00", "keep raw"],
        ],
    )
    client.append_rows(
        "Processed Messages",
        [
            ["old", "test", "2025-05-12 09:00:00", "old raw"],
            ["keep", "test", "2025-05-14 09:00:00", "keep raw"],
        ],
    )
    client.append_rows(
        "Manual Entries",
        [
            ["2025-05-12 09:00:00", "Old", "1", "req", "loc", "budget", "notes", "Manual"],
            ["2025-05-14 09:00:00", "Keep", "2", "req", "loc", "budget", "notes", "Manual"],
        ],
    )

    _prune_old_tracking_data(client, datetime(2026, 5, 14, 12, 0, 0), 365)

    structured = client.get_table_rows("Structured Data")
    raw = client.get_table_rows("Raw Data")
    processed = client.get_table_rows("Processed Messages")
    manual = client.get_table_rows("Manual Entries")

    assert [row["Lead_ID"] for row in structured["rows"]] == ["keep"]
    assert [row["Raw Message"] for row in raw["rows"]] == ["keep raw"]
    assert [row["Fingerprint"] for row in processed["rows"]] == ["keep"]
    assert [row["Name"] for row in manual["rows"]] == ["Keep"]


def test_glide_view_supports_lead_date_range_filter(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            StructuredLead(
                {
                    "Lead_ID": "lead-old",
                    "Name": "Old Lead",
                    "Type": "Buyer",
                    "Location": "Baner",
                    "Date": "2026-05-13",
                    "Last Seen": "2026-05-13 09:00:00",
                    "Cleaned Message": "old cleaned",
                }
            ).to_row(),
            StructuredLead(
                {
                    "Lead_ID": "lead-today",
                    "Name": "Today Lead",
                    "Type": "Buyer",
                    "Location": "Baner",
                    "Date": "2026-05-14",
                    "Last Seen": "2026-05-14 09:00:00",
                    "Cleaned Message": "today cleaned",
                }
            ).to_row(),
        ],
    )

    payload = get_glide_view_dataset(
        client,
        mode="all",
        from_date=date(2026, 5, 14),
        to_date=date(2026, 5, 14),
    )

    assert payload["row_count"] == 1
    assert payload["rows"][0]["lead_id"] == "lead-today"
    assert payload["rows"][0]["cleaned_message"] == "today cleaned"
