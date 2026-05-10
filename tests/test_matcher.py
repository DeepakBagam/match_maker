from datetime import datetime

from app.matcher import compute_matches, demand_summary, supply_summary, top_leads
from app.schemas import StructuredLead


def test_match_threshold_and_components():
    buyer = StructuredLead(
        {
            "Lead_ID": "B1",
            "Name": "Buyer",
            "Type": "Buyer",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 2,
            "Budget_Min": 7000000,
            "Budget_Max": 8000000,
            "Transaction Type": "Sale",
            "Contact Number": "9876543210",
            "Last Seen": "2026-04-03 10:00:00",
            "Confidence Score": 90,
        }
    )
    seller = StructuredLead(
        {
            "Lead_ID": "S1",
            "Name": "Seller",
            "Type": "Seller",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 2,
            "Budget_Min": 7500000,
            "Budget_Max": 8500000,
            "Transaction Type": "Sale",
            "Contact Number": "9876500000",
            "Last Seen": "2026-04-03 10:00:00",
            "Confidence Score": 95,
        }
    )
    weights = {
        "match_location": 2,
        "match_property": 2,
        "match_bhk": 2,
        "match_budget": 2,
        "match_transaction": 1,
        "match_recency": 1,
        "match_completeness": 1,
        "match_confidence": 1,
    }
    rows = compute_matches([buyer, seller], weights, threshold=60, now=datetime(2026, 4, 4, 10, 0, 0))
    assert len(rows) == 1
    assert rows[0]["Location"] == "Wakad"
    assert rows[0]["BHK"] == 2
    assert "Location: Wakad" in rows[0]["Match Reason"]
    assert "Transaction: Sale" in rows[0]["Match Reason"]


def test_raw_rows_are_included_in_structured_matching_outputs():
    seller = StructuredLead(
        {
            "Lead_ID": "S1",
            "Name": "Seller",
            "Type": "Seller",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 2,
            "Budget_Min": 7000000,
            "Budget_Max": 8000000,
            "Transaction Type": "Sale",
            "Contact Number": "9876500000",
            "Last Seen": "2026-04-03 10:00:00",
            "Confidence Score": 90,
            "Priority Score": 75,
            "data_status": "RAW",
        }
    )
    buyer = StructuredLead(
        {
            "Lead_ID": "B1",
            "Name": "Buyer",
            "Type": "Buyer",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 2,
            "Budget_Min": 7000000,
            "Budget_Max": 8000000,
            "Transaction Type": "Sale",
            "Contact Number": "9876543210",
            "Last Seen": "2026-04-03 10:00:00",
            "Confidence Score": 90,
            "Priority Score": 99,
            "data_status": "RAW",
        }
    )

    rows = compute_matches([seller, buyer], {"match_location": 1, "match_property": 1, "match_bhk": 1, "match_budget": 1, "match_transaction": 1, "match_recency": 1, "match_completeness": 1, "match_confidence": 1}, threshold=0, now=datetime(2026, 4, 4, 10, 0, 0))

    assert len(rows) == 1
    assert top_leads([seller, buyer], top_n=5)[0].values["Lead_ID"] == "B1"
    assert demand_summary([seller, buyer])[0]["Location"] == "Wakad"
    assert supply_summary([seller, buyer])[0]["Location"] == "Wakad"
    assert demand_summary([seller, buyer])[0]["Budget_Min"] == 7000000
    assert demand_summary([seller, buyer])[0]["Budget_Max"] == 8000000
    assert supply_summary([seller, buyer])[0]["Price_Min"] == 7000000
    assert supply_summary([seller, buyer])[0]["Price_Max"] == 8000000
