from io import BytesIO
from time import sleep

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app
from app.pipeline import PipelineResult


def test_whatsapp_file_upload_supports_multiple_files(monkeypatch):
    captured = {}

    def fake_client():
        return object()

    def fake_process(_client, parsed):
        captured["parsed"] = parsed
        return PipelineResult(processed=len(parsed), new_rows=len(parsed), duplicates=0, ignored=0, matches=0)

    monkeypatch.setattr("app.main._client", fake_client)
    monkeypatch.setattr("app.main.process_parsed_messages", fake_process)

    client = TestClient(app)
    response = client.post(
        "/ingest/whatsapp-file",
        files=[
            ("file", ("group-alpha.txt", "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad", "text/plain")),
            ("file", ("group-beta.txt", "01/04/2026, 10:35 am - Bob: 2bhk flat available wakad 75L", "text/plain")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["processed"] == 2
    assert [msg.source for msg in captured["parsed"]] == ["group-alpha", "group-beta"]


def test_whatsapp_file_upload_preserves_folder_context_in_source(monkeypatch):
    captured = {}

    def fake_client():
        return object()

    def fake_process(_client, parsed):
        captured["parsed"] = parsed
        return PipelineResult(processed=len(parsed), new_rows=len(parsed), duplicates=0, ignored=0, matches=0)

    monkeypatch.setattr("app.main._client", fake_client)
    monkeypatch.setattr("app.main.process_parsed_messages", fake_process)

    client = TestClient(app)
    response = client.post(
        "/ingest/whatsapp-file",
        files=[
            ("file", ("team-a/group.txt", "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad", "text/plain")),
            ("file", ("team-b/group.txt", "01/04/2026, 10:35 am - Bob: 2bhk flat available wakad 75L", "text/plain")),
        ],
    )

    assert response.status_code == 200
    assert response.json()["processed"] == 2
    assert [msg.source for msg in captured["parsed"]] == ["team-a / group", "team-b / group"]


def test_whatsapp_file_upload_can_run_in_background(monkeypatch):
    def fake_client():
        return object()

    def fake_process(_client, parsed):
        return PipelineResult(processed=len(parsed), new_rows=len(parsed), duplicates=0, ignored=0, matches=0)

    monkeypatch.setattr("app.main._client", fake_client)
    monkeypatch.setattr("app.main.process_parsed_messages", fake_process)

    client = TestClient(app)
    response = client.post(
        "/ingest/whatsapp-file",
        data={"background": "true"},
        files=[
            ("file", ("group-alpha.txt", "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad", "text/plain")),
        ],
    )

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]

    final = None
    for _ in range(20):
        status_response = client.get(f"/jobs/{body['job_id']}")
        assert status_response.status_code == 200
        final = status_response.json()
        if final["status"] == "success":
            break
        sleep(0.05)

    assert final is not None
    assert final["status"] == "success"
    assert final["result"]["processed"] == 1


def test_data_endpoint_returns_sheet_rows(monkeypatch):
    class FakeClient:
        def ensure_structure(self):
            return None

        def get_table_page(self, tab, *, limit=200, offset=0, from_date=None, to_date=None):
            assert tab == "Structured Data"
            rows = [
                {"Date": "2026-05-01", "Source": "Alpha", "Type": "Buyer", "Name": "Asha"},
                {"Date": "2026-05-02", "Source": "Beta", "Type": "Seller", "Name": "Bhavesh"},
            ]
            filtered = [
                row for row in rows
                if (from_date is None or row["Date"] >= from_date) and (to_date is None or row["Date"] <= to_date)
            ]
            return {
                "columns": ["Date", "Source", "Type", "Name"],
                "rows": filtered[offset: offset + limit],
                "row_count": len(filtered),
                "page_size": limit,
                "offset": offset,
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/data?from_date=2026-05-02&to_date=2026-05-02")

    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["Source"] == "Beta"
    assert body["filters"] == {"from_date": "2026-05-02", "to_date": "2026-05-02"}


def test_clear_data_requires_confirm(monkeypatch):
    client = TestClient(app)
    response = client.post(
        "/data/clear",
        json={"mode": "all", "confirm_text": "NOPE", "from_date": None, "to_date": None},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Type CONFIRM to enable deletion"


def test_clear_data_accepts_case_insensitive_confirm(monkeypatch):
    captured = {}

    def fake_clear_structured_data(_client, mode, from_date, to_date):
        captured["mode"] = mode
        captured["from_date"] = from_date
        captured["to_date"] = to_date

        class Result:
            __dict__ = {
                "mode": mode,
                "deleted_rows": 2,
                "remaining_rows": 0,
                "from_date": None,
                "to_date": None,
            }

        return Result()

    monkeypatch.setattr("app.main._client", lambda: object())
    monkeypatch.setattr("app.main.clear_structured_data", fake_clear_structured_data)

    client = TestClient(app)
    response = client.post(
        "/data/clear",
        json={"mode": "all", "confirm_text": " confirm ", "from_date": None, "to_date": None},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Data cleared successfully"
    assert captured["mode"] == "all"


def test_data_export_returns_excel_workbook(monkeypatch):
    class FakeClient:
        def get_table_rows(self, tab, *, from_date=None, to_date=None):
            assert tab == "Structured Data"
            assert from_date == "2026-05-02"
            assert to_date == "2026-05-02"
            return {
                "columns": ["Date", "Source", "Type", "Name"],
                "rows": [
                    {"Date": "2026-05-02", "Source": "Beta", "Type": "Seller", "Name": "Bhavesh"},
                ],
                "row_count": 1,
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/data/export?tab=Structured%20Data&from_date=2026-05-02&to_date=2026-05-02&scope=tab")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "matchlayer_structured_data_2026-05-02_2026-05-02.xlsx" in response.headers["content-disposition"]

    workbook = load_workbook(filename=BytesIO(response.content))
    sheet = workbook.active
    assert sheet.title == "Structured Data"
    assert [sheet["A1"].value, sheet["B1"].value, sheet["C1"].value, sheet["D1"].value] == ["Date", "Source", "Type", "Name"]
    assert [sheet["A2"].value, sheet["B2"].value, sheet["C2"].value, sheet["D2"].value] == ["2026-05-02", "Beta", "Seller", "Bhavesh"]


def test_glide_view_returns_backend_projection(monkeypatch):
    class FakeClient:
        def ensure_structure(self):
            return None

        def get_table(self, tab):
            assert tab == "Structured Data"
            return [
                [
                    "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                    "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                    "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                    "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                    "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                    "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                    "Priority Score", "Priority Reason",
                ],
                [
                    "2099-05-01", "2099-05", "2099-W18", "10:30", "Alpha", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Buyer lead", "Success", "92", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2099-05-01 10:30:00", "2099-05-01 10:30:00", "82", "Very recent",
                ],
                [
                    "2020-05-01", "2020-05", "2020-W18", "10:30", "Beta", "Buyer", "Sale", "Baner",
                    "Apartment", "3", "90-100", "90", "100", "", "", "", "8888888888", "Bala", "", "",
                    "Older lead", "Success", "88", "", "", "", "", "L-002", "C-002", "1", "No",
                    "RAW", "2020-05-01 10:30:00", "2020-05-01 10:30:00", "30", "Standard lead",
                ],
            ]

        def get_table_rows(self, tab, **kwargs):
            assert tab == "Matches"
            return {
                "columns": ["Buyer Lead_ID", "Buyer Name", "Buyer Phone", "Seller Lead_ID", "Seller Name", "Seller Phone", "Location", "Property Type", "BHK", "Buyer Budget", "Seller Budget", "Match Score", "Match Reason", "Matched At", "Date", "Month", "Week"],
                "rows": [
                    {
                        "Buyer Lead_ID": "L-001",
                        "Buyer Name": "Asha",
                        "Buyer Phone": "9999999999",
                        "Seller Lead_ID": "S-001",
                        "Seller Name": "Broker One",
                        "Seller Phone": "7777777777",
                        "Location": "Wakad",
                        "Property Type": "Apartment",
                        "BHK": "2",
                        "Buyer Budget": "70-80",
                        "Seller Budget": "75-78",
                        "Match Score": "91.2",
                        "Match Reason": "Location: Wakad | BHK: 2",
                        "Matched At": "2099-05-01 11:00:00",
                        "Date": "2099-05-01",
                        "Month": "2099-05",
                        "Week": "2099-W18",
                    }
                ],
                "row_count": 1,
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/glide/view?mode=today")

    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["lead_id"] == "L-001"
    assert body["rows"][0]["match_count"] == "1"
    assert body["rows"][0]["calc_in_today_view"] == "TRUE"
    assert body["rows"][0]["match_1_broker_name"] == "Broker One"


def test_glide_view_detail_returns_fixed_match_slots(monkeypatch):
    class FakeClient:
        def ensure_structure(self):
            return None

        def get_table(self, tab):
            return [
                [
                    "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                    "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                    "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                    "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                    "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                    "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                    "Priority Score", "Priority Reason",
                ],
                [
                    "2099-05-01", "2099-05", "2099-W18", "10:30", "Alpha", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Buyer lead", "Success", "92", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2099-05-01 10:30:00", "2099-05-01 10:30:00", "82", "Very recent",
                ],
            ]

        def get_table_rows(self, tab, **kwargs):
            return {
                "columns": [],
                "rows": [
                    {
                        "Buyer Lead_ID": "L-001",
                        "Buyer Name": "Asha",
                        "Buyer Phone": "9999999999",
                        "Seller Lead_ID": "S-001",
                        "Seller Name": "Broker One",
                        "Seller Phone": "7777777777",
                        "Location": "Wakad",
                        "Property Type": "Apartment",
                        "BHK": "2",
                        "Buyer Budget": "70-80",
                        "Seller Budget": "75-78",
                        "Match Score": "91.2",
                        "Match Reason": "Location: Wakad | BHK: 2",
                        "Matched At": "2099-05-01 11:00:00",
                        "Date": "2099-05-01",
                        "Month": "2099-05",
                        "Week": "2099-W18",
                    }
                ],
                "row_count": 1,
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/glide/view/L-001")

    assert response.status_code == 200
    body = response.json()
    assert body["lead_id"] == "L-001"
    assert body["match_1_property_summary"].startswith("Apartment | Wakad")
    assert body["match_1_broker_phone"] == "7777777777"
    assert body["match_2_property_summary"] == ""
    assert body["status"] == ""


def test_glide_readiness_reports_builder_status(monkeypatch):
    class FakeClient:
        def ensure_structure(self):
            return None

        def get_table(self, tab):
            return [[
                "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                "Priority Score", "Priority Reason",
            ]]

        def get_table_rows(self, tab, **kwargs):
            return {"columns": [], "rows": [], "row_count": 0}

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/glide/readiness")

    assert response.status_code == 200
    body = response.json()
    assert any(item["key"] == "glide_tab" and item["status"] == "ready" for item in body["requirements"])
    assert any(item["key"] == "execution_fields" and item["status"] == "ready" for item in body["requirements"])
    assert "status" in body["field_ownership"]["future_execution_fields"]


def test_glide_execution_update_persists_db_backed_fields(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.execution = {}
            self.logs = []
            self.errors = []

        def ensure_structure(self):
            return None

        def get_table(self, tab):
            return [
                [
                    "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                    "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                    "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                    "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                    "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                    "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                    "Priority Score", "Priority Reason",
                ],
                [
                    "2099-05-01", "2099-05", "2099-W18", "10:30", "Alpha", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Buyer lead", "Success", "92", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2099-05-01 10:30:00", "2099-05-01 10:30:00", "82", "Very recent",
                ],
            ]

        def get_table_rows(self, tab, **kwargs):
            return {"columns": [], "rows": [], "row_count": 0}

        def get_glide_execution_map(self):
            return self.execution

        def upsert_glide_execution(self, lead_id, values):
            current = dict(self.execution.get(lead_id, {"Lead_ID": lead_id}))
            current.update(values)
            current["Lead_ID"] = lead_id
            self.execution[lead_id] = current
            return current

        def append_glide_write_log(self, rows):
            self.logs.extend(rows)

        def append_system_errors(self, rows):
            self.errors.extend(rows)

    fake = FakeClient()
    monkeypatch.setattr("app.main._client", lambda: fake)

    client = TestClient(app)
    response = client.post(
        "/glide/view/L-001/execution",
        json={"status": "FOLLOW_UP", "notes": "Called and waiting", "next_follow_up": "2099-05-03"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["detail"]["status"] == "FOLLOW_UP"
    assert body["detail"]["notes"] == "Called and waiting"
    assert body["detail"]["next_follow_up"] == "2099-05-03"
    assert body["detail"]["calc_follow_up_pending"] == "TRUE"
    assert fake.logs


def test_glide_action_logging_updates_last_interaction(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.execution = {"L-001": {"Lead_ID": "L-001", "Status": "CONTACTED", "Notes": "", "Next Follow-up": "", "Write Sync Error": "FALSE"}}
            self.logs = []
            self.errors = []

        def ensure_structure(self):
            return None

        def get_table(self, tab):
            return [
                [
                    "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                    "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                    "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                    "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                    "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                    "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                    "Priority Score", "Priority Reason",
                ],
                [
                    "2099-05-01", "2099-05", "2099-W18", "10:30", "Alpha", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Buyer lead", "Success", "92", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2099-05-01 10:30:00", "2099-05-01 10:30:00", "82", "Very recent",
                ],
            ]

        def get_table_rows(self, tab, **kwargs):
            return {"columns": [], "rows": [], "row_count": 0}

        def get_glide_execution_map(self):
            return self.execution

        def upsert_glide_execution(self, lead_id, values):
            current = dict(self.execution.get(lead_id, {"Lead_ID": lead_id}))
            current.update(values)
            current["Lead_ID"] = lead_id
            self.execution[lead_id] = current
            return current

        def append_glide_write_log(self, rows):
            self.logs.extend(rows)

        def append_system_errors(self, rows):
            self.errors.extend(rows)

    fake = FakeClient()
    monkeypatch.setattr("app.main._client", lambda: fake)

    client = TestClient(app)
    response = client.post("/glide/view/L-001/action", json={"action": "call"})

    assert response.status_code == 200
    body = response.json()
    assert body["detail"]["last_interaction_date"]
    assert fake.logs[-1][5] == "call_lead"


def test_glide_deals_log_endpoints(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.rows = []

        def get_table_page(self, tab, *, limit=200, offset=0, from_date=None, to_date=None):
            assert tab == "Deals Log"
            return {
                "columns": ["Timestamp", "Lead_ID", "Event_Type", "Deal_Status", "Notes", "Created_By"],
                "rows": self.rows[offset: offset + limit],
                "row_count": len(self.rows),
                "page_size": limit,
                "offset": offset,
            }

        def append_rows(self, tab, rows):
            assert tab == "Deals Log"
            for row in rows:
                self.rows.insert(0, {
                    "Timestamp": row[0],
                    "Lead_ID": row[1],
                    "Event_Type": row[2],
                    "Deal_Status": row[3],
                    "Notes": row[4],
                    "Created_By": row[5],
                })

    fake = FakeClient()
    monkeypatch.setattr("app.main._client", lambda: fake)

    client = TestClient(app)
    create = client.post("/glide/deals-log", json={"lead_id": "L-001", "event_type": "site_visit", "deal_status": "open", "notes": "Visited property", "created_by": "Glide"})
    assert create.status_code == 200

    fetch = client.get("/glide/deals-log?limit=10")
    assert fetch.status_code == 200
    body = fetch.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["Event_Type"] == "site_visit"


def test_glide_alerts_leads_endpoints(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.rows = []

        def get_table_page(self, tab, *, limit=200, offset=0, from_date=None, to_date=None):
            assert tab == "Alerts Leads"
            return {
                "columns": ["Timestamp", "Contact", "Source"],
                "rows": self.rows[offset: offset + limit],
                "row_count": len(self.rows),
                "page_size": limit,
                "offset": offset,
            }

        def append_rows(self, tab, rows):
            assert tab == "Alerts Leads"
            for row in rows:
                self.rows.insert(0, {
                    "Timestamp": row[0],
                    "Contact": row[1],
                    "Source": row[2],
                })

    fake = FakeClient()
    monkeypatch.setattr("app.main._client", lambda: fake)

    client = TestClient(app)
    create = client.post("/glide/alerts-leads", json={"contact": "lead@example.com"})
    assert create.status_code == 200

    fetch = client.get("/glide/alerts-leads?limit=10")
    assert fetch.status_code == 200
    body = fetch.json()
    assert body["row_count"] == 1
    assert body["rows"][0]["Contact"] == "lead@example.com"
    assert body["rows"][0]["Source"] == "Website"


def test_glide_filter_config_endpoint_returns_db_backed_values(monkeypatch):
    class FakeClient:
        def get_key_values(self, tab):
            assert tab == "Config"
            return {
                "glide_activity_window_days": 150,
                "glide_recent_interaction_days": 45,
                "glide_priority_qualified_score": 72,
            }

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/glide/filter-config")

    assert response.status_code == 200
    body = response.json()
    assert body["activity_window_days"] == 150
    assert body["recent_interaction_days"] == 45
    assert body["priority_qualified_score"] == 72


def test_glide_today_inclusion_rule_uses_db_config(monkeypatch):
    class FakeClient:
        def ensure_structure(self):
            return None

        def get_key_values(self, tab):
            if tab == "Config":
                return {
                    "glide_activity_window_days": 5,
                    "glide_recent_interaction_days": 2,
                    "glide_priority_qualified_score": 70,
                }
            return {}

        def get_table(self, tab):
            assert tab == "Structured Data"
            return [
                [
                    "Date", "Month", "Week", "Time", "Source", "Type", "Transaction Type", "Location",
                    "Property Type", "BHK", "Budget Range", "Budget_Min", "Budget_Max", "Area_Sqft",
                    "Furnishing", "Project_Name", "Contact Number", "Name", "Raw Message", "Cleaned Message",
                    "Lead Summary", "Extraction Status", "Confidence Score", "location_confidence",
                    "budget_confidence", "bhk_confidence", "Extraction Flags", "Lead_ID", "Contact_ID",
                    "Repeat Count", "Incomplete Data", "data_status", "First Seen", "Last Seen",
                    "Priority Score", "Priority Reason",
                ],
                [
                    "2026-04-28", "2026-04", "2026-W18", "10:30", "Alpha", "Buyer", "Sale", "Wakad",
                    "Apartment", "2", "70-80", "70", "80", "", "", "", "9999999999", "Asha", "", "",
                    "Buyer lead", "Success", "92", "", "", "", "", "L-001", "C-001", "1", "No",
                    "RAW", "2026-04-28 10:30:00", "2026-04-28 10:30:00", "69", "Recent",
                ],
            ]

        def get_table_rows(self, tab, **kwargs):
            return {"columns": [], "rows": [], "row_count": 0}

        def get_glide_execution_map(self):
            return {}

    monkeypatch.setattr("app.main._client", lambda: FakeClient())

    client = TestClient(app)
    response = client.get("/glide/view?mode=today")

    assert response.status_code == 200
    body = response.json()
    assert body["row_count"] == 0
