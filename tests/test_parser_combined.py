from app.parser import parse_combined_whatsapp_export


def test_parse_combined_whatsapp_export_preserves_source_markers():
    text = "\n".join(
        [
            "Source: Group A",
            "[14/05/2026, 10:00 AM] John: Need 2 BHK in Baner",
            "Source: Group B",
            "[14/05/2026, 10:05 AM] Mary: Flat available in Kharadi",
        ]
    )

    entries = parse_combined_whatsapp_export(text, "Default Group")

    assert len(entries) == 2
    assert entries[0].source == "Group A"
    assert entries[1].source == "Group B"
