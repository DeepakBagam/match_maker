from datetime import datetime

from app.parser import filter_recent, parse_combined_whatsapp_export, parse_whatsapp_export


def test_parse_multiline_messages():
    text = (
        "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad\n"
        "Budget 70-80L\n"
        "01/04/2026, 10:35 am - Bob: 2bhk flat available wakad 75L"
    )
    rows = parse_whatsapp_export(text, "WhatsApp Group")
    assert len(rows) == 2
    assert "Budget 70-80L" in rows[0].message
    assert rows[1].sender == "Bob"


def test_parse_bracketed_whatsapp_export_with_seconds():
    text = (
        "[22/08/18, 8:09:23 AM] Noor Properitor Dreamhome: Available 3bhk at Bund Garden\n"
        "For rent 45k\n"
        "[22/08/18, 8:22:13 AM] +91 99756 71117: left"
    )
    rows = parse_whatsapp_export(text, "WhatsApp Group")
    assert len(rows) == 2
    assert rows[0].sender == "Noor Properitor Dreamhome"
    assert "For rent 45k" in rows[0].message
    assert rows[0].timestamp == datetime(2018, 8, 22, 8, 9, 23)


def test_filter_recent_can_be_disabled():
    rows = parse_whatsapp_export("[22/08/18, 8:09:23 AM] A: hello", "WhatsApp Group")
    filtered = filter_recent(rows, 0, datetime(2026, 4, 8, 10, 0, 0))
    assert len(filtered) == 1


def test_parse_combined_whatsapp_export_keeps_group_sources():
    text = (
        "Source: Group Alpha\n"
        "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad\n"
        "Source: Group Beta\n"
        "01/04/2026, 10:35 am - Bob: 2bhk flat available wakad 75L"
    )

    rows = parse_combined_whatsapp_export(text, "Fallback Group")

    assert len(rows) == 2
    assert rows[0].source == "Group Alpha"
    assert rows[1].source == "Group Beta"


def test_parse_combined_whatsapp_export_falls_back_to_default_source():
    text = "01/04/2026, 10:30 am - Alice: Need 2 bhk in wakad"

    rows = parse_combined_whatsapp_export(text, "Fallback Group")

    assert len(rows) == 1
    assert rows[0].source == "Fallback Group"
