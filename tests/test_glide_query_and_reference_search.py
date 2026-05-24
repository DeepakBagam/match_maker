from datetime import datetime

from app.db_client import DatabaseClient
from app.data_management import get_structured_dataset
from app.extractor import MappingResolver, extract_name_from_text, infer_property_type, normalize_text, _normalize_mapped_property_type
from app.glide_builder import get_glide_lead_detail, get_glide_view_dataset
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


def test_reference_rows_capture_multiple_brokers_and_company():
    rows = build_reference_rows(
        [
            StructuredLead(
                {
                    "Lead_ID": "lead-1",
                    "Name": "To View",
                    "Contact Number": "9000000001",
                    "Type": "Seller",
                    "Location": "Kharadi",
                    "Property Type": "Flat",
                    "Date": "2026-05-20",
                    "First Seen": "2026-05-20 09:00:00",
                    "Last Seen": "2026-05-20 10:00:00",
                    "Confidence Score": 90,
                    "data_status": "RAW",
                    "Raw Message": "*Bhatnagar's / CRB Realty*\nDivakar 9370317908\nVijay 9371012398\nCharu 8055894455",
                    "Cleaned Message": "bhatnagar s / crb realty divakar 9370317908 vijay 9371012398 charu 8055894455",
                }
            )
        ]
    )

    broker_index = REFERENCE_DATA_COLUMNS.index("Broker")
    assert rows[0][broker_index] == "Bhatnagar'S / CRB Realty | Divakar | Vijay | Charu"


def test_reference_search_uses_raw_message_content_and_word_boundaries(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            [
                "2026-05-20", "2026-05", "2026-W21", "09:00:00", "Group A", "Seller", "Sale",
                "Kharadi", "Flat", "4", "4.33cr", "43300000", "43300000", "", "", "Eon Waterfront",
                "9000000001", "To View",
                "*Sale*\n*Eon Waterfront*\nPlease DM / Call to view\n*Bhatnagar's / CRB Realty*\nDivakar 9370317908\nVijay 9371012398",
                "sale eon waterfront please dm call to view bhatnagar s crb realty divakar 9370317908 vijay 9371012398",
                "", "Success", "100", "1", "1", "1", "", "lead-1", "contact-1", "1", "No", "RAW",
                "2026-05-20 09:00:00", "2026-05-20 10:00:00", "70", "",
            ],
            [
                "2026-05-20", "2026-05", "2026-W21", "10:00:00", "Group B", "Seller", "Sale",
                "Queensland", "Plot", "", "1cr", "10000000", "10000000", "", "", "",
                "9000000002", "Random",
                "Queensland plot", "queensland plot", "", "Success", "100", "1", "1", "1", "", "lead-2", "contact-2", "1", "No", "RAW",
                "2026-05-20 10:00:00", "2026-05-20 10:00:00", "70", "",
            ],
        ],
    )
    client.append_rows(
        "Reference Data",
        [
            ["lead-1", "To View", "9000000001", "Property", "Seller", "Kharadi", "Flat", "4.33cr", "4", "", "", "2026-05-20 10:00:00", "2026-05-20", "Group A", "Divakar", "RAW", "100", "180", "2026-11-16"],
            ["lead-2", "Random", "9000000002", "Property", "Seller", "Queensland", "Plot", "1cr", "", "", "", "2026-05-20 10:00:00", "2026-05-20", "Group B", "Random", "RAW", "100", "180", "2026-11-16"],
        ],
    )

    payload = client.search_reference_data(query="bhatnagar", limit=0)
    assert payload["row_count"] == 1
    assert payload["rows"][0]["Lead_ID"] == "lead-1"

    payload = client.search_reference_data(location="land", limit=0)
    assert payload["row_count"] == 1
    assert payload["rows"][0]["Lead_ID"] == "lead-2"


def test_reference_search_query_matches_multi_term_broker_tokens(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Reference Data",
        [
            ["lead-1", "Seller One", "9000000001", "Property", "Seller", "Baner", "Flat", "55L", "2", "", "", "2026-05-20 10:00:00", "2026-05-20", "Group A", "Jena Laila", "RAW", "100", "180", "2026-11-16"],
        ],
    )

    payload = client.search_reference_data(query="Laila Jena", limit=10)

    assert payload["row_count"] == 1
    assert payload["rows"][0]["Lead_ID"] == "lead-1"


def test_reference_search_combines_structured_matches_before_pagination(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            _lead("lead-1", "Sana", location="Viman Nagar", lead_type="Seller").to_row(),
            _lead("lead-2", "~ Kushal Sanas", location="Koregaon Park", lead_type="Seller").to_row(),
        ],
    )

    data_payload = get_structured_dataset(client, tab="Structured Data", column_filters={"Name": "sana"}, limit=10)
    payload_small = client.search_reference_data(query="sana", limit=1)
    payload_large = client.search_reference_data(query="sana", limit=10)
    broker_payload = client.search_reference_data(broker="sana", limit=1)

    assert data_payload["row_count"] == 2
    assert payload_small["row_count"] == 2
    assert payload_large["row_count"] == 2
    assert broker_payload["row_count"] == 2
    assert len(payload_small["rows"]) == 1
    assert [row["Lead_ID"] for row in payload_large["rows"]] == ["lead-2", "lead-1"]


def test_reference_search_query_and_location_align_for_primary_location_terms(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            _lead("lead-1", "Seller One", location="Koregaon Park", lead_type="Seller").to_row(),
            _lead("lead-2", "Seller Two", location="Koregaon Park", lead_type="Seller").to_row(),
            _lead("lead-3", "Seller Three", location="Baner", lead_type="Seller").to_row(),
        ],
    )

    data_payload = get_structured_dataset(client, tab="Structured Data", column_filters={"Location": "Koregaon Park"}, limit=10)
    location_payload = client.search_reference_data(location="Koregaon Park", limit=10)

    assert data_payload["row_count"] == 2
    assert location_payload["row_count"] == 2
    assert [row["Lead_ID"] for row in location_payload["rows"]] == ["lead-2", "lead-1"]


def test_reference_search_falls_back_to_structured_only_rows(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [[
            "2026-05-20", "2026-05", "2026-W21", "09:00:00", "_chat", "Ignore", "",
            "", "", "", "", "", "", "", "", "",
            "", "Laila Jena", "image omitted", "image omitted", "", "", "", "", "", "", "", "", "", "", "", "RAW",
            "2026-05-20 09:00:00", "2026-05-20 09:00:00", "", "",
        ]],
    )

    payload = client.search_reference_data(query="Laila Jena", limit=10)

    assert payload["row_count"] == 1
    assert payload["rows"][0]["Name"] == "Laila Jena"
    assert payload["rows"][0]["Entry_Type"] == "Structured Only"
    assert payload["rows"][0]["Lead_ID"].startswith("structured-only:")


def test_reference_search_preserves_duplicate_structured_only_rows(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    duplicate_row = [
        "2026-05-20", "2026-05", "2026-W21", "09:00:00", "_chat", "Ignore", "",
        "", "", "", "", "", "", "", "", "",
        "", "Laila Jena", "image omitted", "image omitted", "", "", "", "", "", "", "", "", "", "", "", "RAW",
        "2026-05-20 09:00:00", "2026-05-20 09:00:00", "", "",
    ]
    client.append_rows("Structured Data", [duplicate_row, duplicate_row])

    payload = client.search_reference_data(query="Laila Jena", limit=10)

    assert payload["row_count"] == 2
    assert len(payload["rows"]) == 2


def test_structured_only_search_result_opens_in_glide_detail(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [[
            "2026-05-20", "2026-05", "2026-W21", "09:00:00", "_chat", "Ignore", "",
            "", "", "", "", "", "", "", "", "",
            "", "Laila Jena", "image omitted", "image omitted", "", "", "", "", "", "", "", "", "", "", "", "RAW",
            "2026-05-20 09:00:00", "2026-05-20 09:00:00", "", "",
        ]],
    )
    payload = client.search_reference_data(query="Laila Jena", limit=10)

    detail = get_glide_lead_detail(client, payload["rows"][0]["Lead_ID"])

    assert detail is not None
    assert detail["structured_only"] == "TRUE"
    assert detail["name"] == "Laila Jena"
    assert detail["match_count"] == ""


def test_data_tab_supports_global_search_and_sort(tmp_path):
    client = _client(tmp_path)
    client.ensure_structure()
    client.append_rows(
        "Structured Data",
        [
            _lead("lead-1", "Gamma", location="Baner").to_row(),
            _lead("lead-2", "Alpha", location="Kharadi").to_row(),
        ],
    )

    payload = get_structured_dataset(client, tab="Structured Data", search="kharadi", sort_column="Name", sort_direction="asc")

    assert payload["row_count"] == 1
    assert payload["rows"][0]["Name"] == "Alpha"


def test_extract_name_ignores_to_view_phrase():
    text = "Please DM / Call to view\nBhatnagar's / CRB Realty\nDivakar 9370317908\nVijay 9371012398"
    assert extract_name_from_text(text) == "Divakar"


def test_plot_with_bungalow_is_normalized_to_villa():
    cleaned = normalize_text("5.5 bhk bunglow for sale 6000 sqft plot 6000 sqft built up")
    assert _normalize_mapped_property_type(cleaned, "Plot", "bungalow", 6) == "Villa"


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
