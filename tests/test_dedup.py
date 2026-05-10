from app.dedup import deduplicate
from app.schemas import StructuredLead


def _lead(phone: str, msg: str, first_seen: str, **extra):
    return StructuredLead(
        {
            "Contact Number": phone,
            "Name": "A",
            "Date": "2026-04-01",
            "Raw Message": msg,
            "First Seen": first_seen,
            "Last Seen": first_seen,
            "Repeat Count": 1,
            **extra,
        }
    )


def test_dedup_with_phone_priority_and_exact_message():
    existing = [_lead("9999999999", "hello", "2026-04-01 10:00:00")]
    incoming = [_lead("9999999999", "hello", "2026-04-01 12:00:00")]
    result = deduplicate(existing, incoming, dedup_days=1)
    assert result.duplicate_count == 1
    assert result.new_count == 0
    assert result.leads[0].values["Repeat Count"] == 2


def test_dedup_keeps_distinct_split_listings_from_same_bulk_message():
    first = _lead(
        "9999999999",
        "bulk-message",
        "2026-04-01 10:00:00",
        Type="Seller",
        **{
            "Transaction Type": "Rent",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 2,
            "Budget_Min": 35000,
            "Budget_Max": 35000,
        },
    )
    second = _lead(
        "9999999999",
        "bulk-message",
        "2026-04-01 10:05:00",
        Type="Seller",
        **{
            "Transaction Type": "Rent",
            "Location": "Wakad",
            "Property Type": "Apartment",
            "BHK": 3,
            "Budget_Min": 45000,
            "Budget_Max": 45000,
        },
    )

    result = deduplicate([], [first, second], dedup_days=1)

    assert result.duplicate_count == 0
    assert result.new_count == 2
    assert len(result.leads) == 2
