from datetime import datetime

from app.db_client import DatabaseClient
from app.glide_builder import get_glide_view_dataset
from app.reference_data import REFERENCE_DATA_COLUMNS, build_reference_rows
from app.schemas import StructuredLead


def _client(tmp_path) -> DatabaseClient:
    return DatabaseClient(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


def _lead(
    lead_id: str,
    name: str,
    *,
    lead_type: str = "Buyer",
    location: str = "Baner",
    property_type: str = "Flat",
    date: str = "2026-05-20",
    last_seen: str = "2026-05-20 09:00:00",
    phone: str = "9999999999",
) -> StructuredLead:
    return StructuredLead(
        {
            "Lead_ID": lead_id,
            "Name": name,
            "Type": lead_type,
            "Location": location,
            "Property Type": property_type,
            "Transaction Type": "Sale",
            "Contact Number": phone,
            "Date": date,
            "Last Seen": last_seen,
            "Cleaned Message": f"{name} cleaned",
            "Priority Score": 65,
            "Budget_Min": 5000000,
            "Budget_Max": 6000000,
        }
    )


def test_glide_view_uses_server_side_pagination(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            _lead("lead-1", "Alpha").to_row(),
            _lead("lead-2", "Beta").to_row(),
            _lead("lead-3", "Gamma").to_row(),
        ],
    )

    payload = get_glide_view_dataset(
        client,
        mode="all",
        limit=1,
        offset=1,
        now=datetime(2026, 5, 20, 12, 0, 0),
    )

    assert payload["row_count"] == 3
    assert payload["page_size"] == 1
    assert [row["name"] for row in payload["rows"]] == ["Beta"]


def test_glide_view_search_matches_counterparty_broker_name(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows("Structured Data", [_lead("buyer-1", "Primary Buyer").to_row()])
    client.append_rows(
        "Matches",
        [[
            "2026-05-20",
            "2026-05",
            "2026-W21",
            "buyer-1",
            "Primary Buyer",
            "9000000001",
            "seller-1",
            "Abdul Broker",
            "9000000002",
            "Baner",
            "Flat",
            "2",
            "55L",
            "58L",
            "92",
            "Location and budget aligned",
            "2026-05-20 10:30:00",
        ]],
    )

    payload = get_glide_view_dataset(
        client,
        mode="all",
        search="abdul",
        now=datetime(2026, 5, 20, 12, 0, 0),
    )

    assert payload["row_count"] == 1
    assert payload["rows"][0]["lead_id"] == "buyer-1"
    assert payload["rows"][0]["match_count"] == "1"


def test_reference_search_limit_zero_returns_all_matches(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Reference Data",
        [
            ["lead-1", "Broker One", "9000000001", "Property", "Seller", "Baner", "Flat", "55L", "2", "", "", "2026-05-20 09:00:00", "2026-05-20", "Group A", "Broker One", "RAW", "90", "180", "2026-11-16"],
            ["lead-2", "Broker One", "9000000002", "Property", "Seller", "Baner", "Flat", "57L", "2", "", "", "2026-05-20 08:00:00", "2026-05-20", "Group A", "Broker One", "RAW", "90", "180", "2026-11-16"],
            ["lead-3", "Broker Two", "9000000003", "Requirement", "Buyer", "Kharadi", "Flat", "60L", "2", "", "", "2026-05-20 07:00:00", "2026-05-20", "Group B", "Broker Two", "RAW", "90", "180", "2026-11-16"],
        ],
    )

    payload = client.search_reference_data(query="broker", limit=0)

    assert payload["row_count"] == 3
    assert len(payload["rows"]) == 3


def test_reference_search_property_filter_matches_bhk_and_returns_latest_first(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Reference Data",
        [
            ["lead-1", "Older Two BHK", "9000000001", "Property", "Seller", "Baner", "Flat", "55L", "2", "", "", "2026-05-20 08:00:00", "2026-05-20", "Group A", "Broker One", "RAW", "90", "180", "2026-11-16"],
            ["lead-2", "Newest Two BHK", "9000000002", "Property", "Seller", "Baner", "Flat", "57L", "2", "", "", "2026-05-20 09:00:00", "2026-05-20", "Group A", "Broker One", "RAW", "90", "180", "2026-11-16"],
            ["lead-3", "Three BHK", "9000000003", "Property", "Seller", "Baner", "Flat", "60L", "3", "", "", "2026-05-20 10:00:00", "2026-05-20", "Group B", "Broker Two", "RAW", "90", "180", "2026-11-16"],
        ],
    )

    payload = client.search_reference_data(property_type="2")

    assert payload["row_count"] == 2
    assert [row["Lead_ID"] for row in payload["rows"]] == ["lead-2", "lead-1"]


def test_reference_rows_normalize_broker_name():
    rows = build_reference_rows(
        [
            StructuredLead(
                {
                    "Lead_ID": "lead-1",
                    "Name": "Call Abdul",
                    "Contact Number": "9000000001",
                    "Type": "Seller",
                    "Location": "Baner",
                    "Property Type": "Flat",
                    "Date": "2026-05-20",
                    "First Seen": "2026-05-20 09:00:00",
                    "Last Seen": "2026-05-20 10:00:00",
                    "Confidence Score": 90,
                    "data_status": "RAW",
                }
            )
        ]
    )

    broker_index = REFERENCE_DATA_COLUMNS.index("Broker")
    assert rows[0][broker_index] == "Abdul"


def test_glide_view_orders_latest_leads_first(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            _lead("lead-older", "Older Lead", last_seen="2026-05-20 08:00:00").to_row(),
            _lead("lead-newer", "Newer Lead", last_seen="2026-05-20 10:00:00").to_row(),
        ],
    )

    payload = get_glide_view_dataset(
        client,
        mode="all",
        now=datetime(2026, 5, 20, 12, 0, 0),
    )

    assert [row["lead_id"] for row in payload["rows"][:2]] == ["lead-newer", "lead-older"]
