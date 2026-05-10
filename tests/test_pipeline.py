from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.pipeline import process_manual_entry, process_parsed_messages
from app.db_client import DatabaseClient
from app.schemas import ParsedMessage, STRUCTURED_COLUMNS
from app.sheets_client import DEFAULT_CONFIG, DEFAULT_WEIGHTS, PROCESSED_MESSAGE_COLUMNS


class FakeClient:
    def __init__(self, *, config=None):
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.weights = dict(DEFAULT_WEIGHTS)
        self.tables = {
            "Processed Messages": [PROCESSED_MESSAGE_COLUMNS],
            "Structured Data": [STRUCTURED_COLUMNS],
            "Location Mapping": [["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]],
            "Property Type Mapping": [["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]],
        }
        self.appended = []
        self.replaced = {}
        self.clean_data_formula_synced = False

    def ensure_structure(self):
        return None

    def get_key_values(self, tab: str):
        if tab == "Config":
            return self.config
        if tab == "Scoring Weights":
            return self.weights
        return {}

    def read_structured(self):
        return self.tables["Structured Data"]

    def read_processed_messages(self):
        return self.tables["Processed Messages"]

    def get_table(self, tab: str):
        return self.tables.get(tab, [])

    def replace_rows(self, tab: str, header: list[str], rows: list[list[object]]):
        self.replaced[tab] = [header, *rows]
        self.tables[tab] = [header, *rows]

    def replace_many_rows(self, payloads: dict[str, tuple[list[str], list[list[object]]]]):
        for tab, (header, rows) in payloads.items():
            self.replace_rows(tab, header, rows)

    def append_rows(self, tab: str, rows: list[list[object]]):
        self.appended.append((tab, rows))
        self.tables.setdefault(tab, []).extend(rows)

    def sync_clean_data_formula(self):
        self.clean_data_formula_synced = True


def test_whatsapp_processing_applies_dynamic_lookback(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 8, 10, 0, 0))
    client = FakeClient(config={"lookback_days": 2})
    parsed = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Recent",
            message="Need 2 bhk",
            raw_message="Need 2 bhk",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 1, 9, 0, 0),
            sender="Old",
            message="Need 2 bhk",
            raw_message="Need 2 bhk",
            source="WhatsApp Group",
        ),
    ]

    result = process_parsed_messages(client, parsed)

    assert result.processed == 1
    raw_rows = next(rows for tab, rows in reversed(client.appended) if tab == "Raw Data")
    assert len(raw_rows) == 1
    assert raw_rows[0][2] == "Recent"


def test_manual_processing_skips_lookback(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 8, 10, 0, 0))
    client = FakeClient(config={"lookback_days": 2})

    result = process_manual_entry(
        client,
        name="Rahul",
        phone="9876543210",
        requirement="Need 2 bhk",
        location="Wakad",
        budget="70L",
        notes="Old manual lead allowed",
        source="Manual",
    )

    assert result.processed == 1
    assert client.appended[0][0] == "Manual Entries"
    assert client.appended[1][0] == "Raw Data"
    assert len(client.appended[1][1]) == 1


def test_validation_checkpoint_is_written_from_latest_batch(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 8, 10, 0, 0))
    client = FakeClient(config={"validation_sample_size": 1})
    parsed = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Alpha Broker",
            message="Available on rent 2 bhk",
            raw_message="Available on rent 2 bhk",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 5, 0),
            sender="Beta Broker",
            message="Available on rent 3 bhk",
            raw_message="Available on rent 3 bhk",
            source="Direct WhatsApp",
        ),
    ]

    process_parsed_messages(client, parsed, apply_lookback=False)

    validation = client.replaced["Validation Checkpoint"]
    assert validation[0][0] == "Date"
    assert len(validation) == 2
    assert validation[1][2] == "WhatsApp Group"
    assert validation[1][3] == "Alpha Broker"
    assert client.clean_data_formula_synced is True


def test_match_and_top_lead_validation_tabs_are_written(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 8, 10, 0, 0))
    client = FakeClient(
        config={
            "match_threshold": 0,
            "match_validation_sample_size": 1,
            "top_leads_count": 2,
            "top_leads_validation_size": 1,
        }
    )
    client.tables["Location Mapping"] = [
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["wakad", "Wakad", "wakad", ""],
    ]
    client.tables["Property Type Mapping"] = [
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["flat", "Apartment", "flat,apartment", ""],
    ]
    parsed = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Buyer One 9876543210",
            message="Need 2 bhk flat in wakad budget 70-80L sale",
            raw_message="Need 2 bhk flat in wakad budget 70-80L sale",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 5, 0),
            sender="Seller One 9876500000",
            message="Available 2 bhk flat in wakad budget 75-85L sale",
            raw_message="Available 2 bhk flat in wakad budget 75-85L sale",
            source="Manual",
        ),
    ]

    process_parsed_messages(client, parsed, apply_lookback=False)

    match_validation = client.replaced["Match Validation Checkpoint"]
    top_validation = client.replaced["Top Leads Validation"]
    assert match_validation[0][3] == "Buyer Lead_ID"
    assert len(match_validation) == 2
    assert match_validation[1][4] == "Buyer One"
    assert top_validation[0][0] == "Lead_ID"
    assert len(top_validation) == 2


def test_final_validation_reports_mixed_source_end_to_end_status(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 8, 10, 0, 0))
    client = FakeClient(
        config={
            "match_threshold": 0,
            "top_leads_count": 5,
        }
    )
    client.tables["Location Mapping"] = [
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["wakad", "Wakad", "wakad", ""],
    ]
    client.tables["Property Type Mapping"] = [
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["flat", "Apartment", "flat,apartment", ""],
    ]

    whatsapp = ParsedMessage(
        timestamp=datetime(2026, 4, 8, 9, 0, 0),
        sender="Buyer One 9876543210",
        message="Need 2 bhk flat in wakad budget 70-80L sale",
        raw_message="Need 2 bhk flat in wakad budget 70-80L sale",
        source="WhatsApp Group",
    )
    process_parsed_messages(client, [whatsapp], apply_lookback=False)

    manual_result = process_manual_entry(
        client,
        name="Seller One",
        phone="9876500000",
        requirement="Available 2 bhk flat sale",
        location="wakad",
        budget="75-85L",
        notes="ready to close",
        source="Manual",
    )

    assert manual_result.matches >= 1
    final_validation = client.replaced["Final Validation"]
    rows = {row[0]: row[1:] for row in final_validation[1:]}
    assert rows["Dataset WhatsApp Rows"][0] == 1
    assert rows["Dataset WhatsApp Rows"][1] == "PASS"
    assert rows["Dataset Manual Rows"][0] == 1
    assert rows["Dataset Manual Rows"][1] == "PASS"
    assert rows["Data Flow Check"][1] == "PASS"
    assert rows["Matches Generated"][1] == "PASS"
    assert rows["Top Leads Generated"][1] == "PASS"
    assert rows["Summaries Accurate"][1] == "PASS"


def test_repeat_full_export_only_processes_new_messages(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 9, 10, 0, 0))
    client = FakeClient(config={"lookback_days": 7})

    first_batch = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Alpha",
            message="Need 2 bhk in wakad",
            raw_message="Need 2 bhk in wakad",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 5, 0),
            sender="Beta",
            message="Available 2 bhk in wakad",
            raw_message="Available 2 bhk in wakad",
            source="WhatsApp Group",
        ),
    ]

    first_result = process_parsed_messages(client, first_batch)

    second_batch = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Alpha",
            message="Need 2 bhk in wakad",
            raw_message="Need 2 bhk in wakad",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 5, 0),
            sender="Beta",
            message="Available 2 bhk in wakad",
            raw_message="Available 2 bhk in wakad",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 9, 9, 10, 0),
            sender="Gamma",
            message="Need 3 bhk in baner",
            raw_message="Need 3 bhk in baner",
            source="WhatsApp Group",
        ),
    ]

    second_result = process_parsed_messages(client, second_batch)

    assert first_result.processed == 2
    assert first_result.new_rows == 2
    assert second_result.processed == 1
    assert second_result.new_rows == 1
    assert second_result.duplicates == 2
    raw_rows = next(rows for tab, rows in reversed(client.appended) if tab == "Raw Data")
    assert len(raw_rows) == 1
    assert raw_rows[0][2] == "Gamma"


def test_persistent_processed_log_blocks_reingest_after_structured_data_is_deleted(monkeypatch):
    monkeypatch.setattr("app.pipeline._now", lambda: datetime(2026, 4, 9, 10, 0, 0))
    client = FakeClient(config={"lookback_days": 7})

    batch = [
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 0, 0),
            sender="Alpha",
            message="Need 2 bhk in wakad",
            raw_message="Need 2 bhk in wakad",
            source="WhatsApp Group",
        ),
        ParsedMessage(
            timestamp=datetime(2026, 4, 8, 9, 5, 0),
            sender="Beta",
            message="Available 2 bhk in wakad",
            raw_message="Available 2 bhk in wakad",
            source="WhatsApp Group",
        ),
    ]

    first_result = process_parsed_messages(client, batch)
    client.tables["Structured Data"] = [STRUCTURED_COLUMNS]

    second_result = process_parsed_messages(client, batch)

    assert first_result.processed == 2
    assert first_result.new_rows == 2
    assert second_result.processed == 0
    assert second_result.new_rows == 0
    assert second_result.duplicates == 2


def test_clean_data_falls_back_to_structured_rows_when_no_approved_rows():
    db_path = Path("tests") / f"clean_data_fallback_{uuid4().hex}.sqlite"
    client = DatabaseClient(f"sqlite:///{db_path.as_posix()}")
    try:
        client.ensure_structure()
        client.replace_rows(
            "Structured Data",
            STRUCTURED_COLUMNS,
            [[
                "2026-04-08", "2026-04", "2026-W15", "10:00", "WhatsApp Group", "Buyer", "Sale", "Wakad",
                "Apartment", "2", "70-80L", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                "Need 2 bhk", "Success", "90", "", "", "", "", "L-001", "C-001", "1", "No",
                "RAW", "2026-04-08 10:00:00", "2026-04-08 10:00:00", "75", "Recent",
            ]]
        )
        client.sync_clean_data_formula()

        clean = client.get_table("Clean Data")
        assert len(clean) == 2
        assert clean[1][0] == "2026-04-08"
        assert clean[1][3] == "Buyer"
        assert clean[1][5] == "Wakad"
    finally:
        if db_path.exists():
            for suffix in ("", "-shm", "-wal"):
                candidate = Path(f"{db_path}{suffix}")
                if candidate.exists():
                    try:
                        candidate.unlink()
                    except PermissionError:
                        pass


def test_clean_data_prefers_approved_rows_when_present():
    db_path = Path("tests") / f"clean_data_approved_{uuid4().hex}.sqlite"
    client = DatabaseClient(f"sqlite:///{db_path.as_posix()}")
    try:
        client.ensure_structure()
        client.replace_rows(
            "Structured Data",
            STRUCTURED_COLUMNS,
            [
                [
                    "2026-04-08", "2026-04", "2026-W15", "10:00", "WhatsApp Group", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80L", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Need 2 bhk", "Success", "90", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2026-04-08 10:00:00", "2026-04-08 10:00:00", "75", "Recent",
                ],
                [
                    "2026-04-09", "2026-04", "2026-W15", "11:00", "Manual", "Seller", "Sale", "Baner",
                    "Apartment", "3", "90-100L", "90", "100", "", "", "", "8888888888", "Bala", "", "",
                    "Available 3 bhk", "Success", "88", "", "", "", "", "L-002", "C-002", "1", "No",
                    "APPROVED", "2026-04-09 11:00:00", "2026-04-09 11:00:00", "81", "Recent",
                ],
            ]
        )
        client.sync_clean_data_formula()

        clean = client.get_table("Clean Data")
        assert len(clean) == 2
        assert clean[1][0] == "2026-04-09"
        assert clean[1][3] == "Seller"
        assert clean[1][5] == "Baner"
    finally:
        if db_path.exists():
            for suffix in ("", "-shm", "-wal"):
                candidate = Path(f"{db_path}{suffix}")
                if candidate.exists():
                    try:
                        candidate.unlink()
                    except PermissionError:
                        pass
