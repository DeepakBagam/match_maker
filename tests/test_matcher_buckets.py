from datetime import datetime

from app.matcher import compute_matches
from app.schemas import StructuredLead


def _lead(
    lead_id: str,
    lead_type: str,
    *,
    location: str,
    property_type: str,
    transaction: str = "Rent",
    phone: str = "9999999999",
    bhk: int | None = 2,
    budget_min: int = 2000000,
    budget_max: int = 3000000,
) -> StructuredLead:
    return StructuredLead(
        {
            "Lead_ID": lead_id,
            "Name": lead_id,
            "Type": lead_type,
            "Location": location,
            "Property Type": property_type,
            "Transaction Type": transaction,
            "Contact Number": phone,
            "BHK": bhk,
            "Budget_Min": budget_min,
            "Budget_Max": budget_max,
            "Confidence Score": 90,
            "Last Seen": "2026-05-14 10:00:00",
            "Cleaned Message": "",
        }
    )


def test_compute_matches_uses_exact_bucket_keys():
    buyer = _lead("buyer-1", "Buyer", location="Koregaon Park", property_type="Flat")
    matching_seller = _lead("seller-1", "Seller", location="Koregaon Park", property_type="Flat")
    wrong_location = _lead("seller-2", "Seller", location="Baner", property_type="Flat")
    wrong_property = _lead("seller-3", "Seller", location="Koregaon Park", property_type="Office")

    matches = compute_matches(
        [buyer, matching_seller, wrong_location, wrong_property],
        weights={},
        threshold=40,
        now=datetime(2026, 5, 14, 12, 0, 0),
    )

    assert len(matches) == 1
    assert matches[0]["Seller Lead_ID"] == "seller-1"


def test_compute_matches_budget_band_limits_candidates_without_losing_overlap_match():
    buyer = _lead("buyer-1", "Buyer", location="Kharadi", property_type="Flat", budget_min=4500000, budget_max=5500000)
    near_budget = _lead("seller-1", "Seller", location="Kharadi", property_type="Flat", budget_min=5000000, budget_max=5200000)
    far_budget = _lead("seller-2", "Seller", location="Kharadi", property_type="Flat", budget_min=9000000, budget_max=9500000)

    matches = compute_matches(
        [buyer, near_budget, far_budget],
        weights={},
        threshold=40,
        now=datetime(2026, 5, 14, 12, 0, 0),
    )

    assert len(matches) == 1
    assert matches[0]["Seller Lead_ID"] == "seller-1"


def test_compute_matches_handles_tied_scores_without_heap_type_error():
    buyer = _lead("buyer-1", "Buyer", location="Kharadi", property_type="Flat", phone="9000000001")
    seller_a = _lead("seller-1", "Seller", location="Kharadi", property_type="Flat", phone="9000000002")
    seller_b = _lead("seller-1", "Seller", location="Kharadi", property_type="Flat", phone="9000000002")

    matches = compute_matches(
        [buyer, seller_a, seller_b],
        weights={},
        threshold=40,
        now=datetime(2026, 5, 14, 12, 0, 0),
    )

    assert len(matches) == 1
    assert matches[0]["Seller Lead_ID"] == "seller-1"
