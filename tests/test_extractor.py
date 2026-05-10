from datetime import datetime

from app.extractor import MappingResolver, build_contact_id, extract_budget, to_structured
from app.parser import parse_whatsapp_export
from app.schemas import ParsedMessage


def test_extraction_and_mapping():
    location_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["wakad", "Wakad", "wakad, waked", ""],
    ])
    property_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["flat", "Apartment", "flat, apartment", ""],
    ])
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="A",
        message="Need 2 bhk flat in wakad budget 70-80L",
        source="WhatsApp Group",
    )
    lead = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})[0]
    assert lead.values["Type"] == "Buyer"
    assert lead.values["Month"] == "2026-04"
    assert lead.values["Week"] == "2026-W14"
    assert lead.values["Location"] == "Wakad"
    assert lead.values["Property Type"] == "Apartment"
    assert lead.values["BHK"] == 2
    assert lead.values["Budget Range"] == "30L-80L"
    assert lead.values["Budget_Min"] == 7000000
    assert lead.values["Budget_Max"] == 8000000
    assert lead.values["Name"] == "A"
    assert lead.values["data_status"] == "RAW"
    assert "Location=Wakad" in lead.values["Lead Summary"]
    assert "Property=Apartment" in lead.values["Lead Summary"]
    assert "Flags=phone_missing,transaction_missing" in lead.values["Lead Summary"]
    assert "transaction_missing" in lead.values["Extraction Flags"]
    assert "phone_missing" in lead.values["Extraction Flags"]


def test_mapping_tables_are_required_for_location_and_property():
    location_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    property_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Rahul +91 9876543210",
        message="Need 2 bhk flat in wakad budget 70-80L",
        source="WhatsApp Group",
    )

    lead = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})[0]

    assert lead.values["Location"] == "Wakad"
    assert lead.values["Property Type"] == "Apartment"
    assert lead.values["BHK"] == 2
    assert lead.values["Contact Number"] == "9876543210"
    assert lead.values["Name"] == "Rahul"
    assert lead.values["data_status"] == "RAW"
    assert "location_missing" not in lead.values["Extraction Flags"]
    assert "property_missing" not in lead.values["Extraction Flags"]


def test_optional_tags_in_mapping_tables_improve_normalization():
    location_map = MappingResolver(
        [["Raw Value", "Canonical Value", "Aliases", "Optional Tags"], ["kumar kruti", "Kalyani Nagar", "", "kumar kruti"]]
    )
    property_map = MappingResolver(
        [["Raw Value", "Canonical Value", "Aliases", "Optional Tags"], ["furnished apartment", "Apartment", "", "ff,furnished"]]
    )
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Broker",
        message="Available on rent 3 bhk ff Kumar Kruti 55k Subhash: 8626025198",
        source="WhatsApp Group",
    )

    lead = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})[0]

    assert lead.values["Type"] == "Seller"
    assert lead.values["Transaction Type"] == "Rent"
    assert lead.values["Location"] == "Kalyani Nagar"
    assert lead.values["Property Type"] == "Apartment"
    assert lead.values["Contact Number"] == "8626025198"


def test_bulk_rental_message_is_split_into_multiple_structured_leads():
    location_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["b suncity signature", "Kalyani Nagar", "b suncity signature, kumar kruti, sovereign soc, siddharth ganga, marigold soc, sky lounge soc, graficorn court, landmark garden, aghakhan palace", ""],
        ]
    )
    property_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["ff", "Apartment", "ff, furnished, comm", ""],
        ]
    )
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Broker Desk",
        message=(
            "*Avl Rental Property*\n"
            "3Bhk ff *B Suncity signature* 55k\n"
            "3.5bhk *Kumar Kruti* Kn 32k\n"
            "3Bhk *Sovereign soc* Kn 38k\n"
            "2bhk *Landmark Garden* Kn 35k\n"
            "*Sunshine Properties*\n"
            "Subhash: 8626025198"
        ),
        raw_message=(
            "*Avl Rental Property*\n"
            "3Bhk ff *B Suncity signature* 55k\n"
            "3.5bhk *Kumar Kruti* Kn 32k\n"
            "3Bhk *Sovereign soc* Kn 38k\n"
            "2bhk *Landmark Garden* Kn 35k\n"
            "*Sunshine Properties*\n"
            "Subhash: 8626025198"
        ),
        source="WhatsApp Group",
    )

    leads = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})

    assert len(leads) == 4
    assert all(lead.values["Type"] == "Seller" for lead in leads)
    assert all(lead.values["Contact Number"] == "8626025198" for lead in leads)
    assert all(lead.values["Name"] == "Subhash" for lead in leads)
    assert all(lead.values["Raw Message"] == msg.raw_message for lead in leads)
    assert leads[0].values["Budget_Min"] == 55000
    assert leads[0].values["Budget_Max"] == 55000
    assert leads[0].values["BHK"] == 3
    assert leads[1].values["BHK"] == 4
    assert "bhk_ambiguous" not in leads[1].values["Extraction Flags"]


def test_budget_extraction_does_not_use_phone_number_as_budget():
    budget_min, budget_max, inferred = extract_budget(
        "budget apmr pls revert sunshine properties subhash 8626025198"
    )

    assert budget_min is None
    assert budget_max is None
    assert inferred is False


def test_name_and_source_are_preserved_when_phone_is_missing():
    location_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    property_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Sunshine Properties",
        message="Need 2 bhk flat in wakad budget 70-80L",
        source="Direct WhatsApp",
    )

    lead = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})[0]

    assert lead.values["Contact Number"] == ""
    assert lead.values["Name"] == "Sunshine Properties"
    assert lead.values["Source"] == "Direct WhatsApp"
    assert lead.values["Contact_ID"] == build_contact_id("", "Sunshine Properties")


def test_hybrid_multisection_broker_message_is_split_with_context():
    location_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["wadgoan sheri", "Wadgaon Sheri", "wadgoan sheri,wadgaon sheri", ""],
            ["kalyaninagar", "Kalyani Nagar", "kalyaninagar,kalyani nagar", ""],
            ["koregaon park", "Koregaon Park", "koregaon park,clover dale", ""],
            ["amnnora", "Amanora", "amnnora,amanora", ""],
            ["viman nagar", "Viman Nagar", "viman nagar,platinum square", ""],
        ]
    )
    property_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["office", "Office", "office,offices,sq ft,sq.ft,sqft,psf", ""],
            ["studio apartment", "Studio Apartment", "studio apartment,studio", ""],
        ]
    )
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 8, 10, 0, 0),
        sender="Broker",
        source="WhatsApp Group",
        message=(
            "🏡 Available on Rent 🏡\n"
            "*Wadgoan Sheri*\n"
            "*Konark Splendour*\n"
            "2bhk Fully Furnished, Garden facing, 32K\n"
            "1st September.\n"
            "*Kalyaninagar*\n"
            "*Sovereign*\n"
            "3bhk sf, 42k. Nego.\n"
            "Immediate.\n"
            "*Koregaon Park*\n"
            "*Clover Dale*\n"
            "Studio Apartment Fully Furnished with Huge Terrace\n"
            "25K Possession From 15 August\n"
            " *Amnnora*\n"
            "3bhk with servent room, lavishly furnished with all white goods\n"
            "60k, nego. immidiete. Family & company lease\n"
            "*Offices on Lease*\n"
            "*Viman Nagar*\n"
            "750 Sq.ft Fully Furnished\n"
            "50 Per Sq.ft\n"
            "750 Sq.ft Unfurnish\n"
            "45 Per Sq.ft\n"
            "*Platinum Square*\n"
            "800 Sq.ft Unfurnish 55 Per Sq.ft\n"
            "For further details call\n"
            "S Qube Properties\n"
            "*Sajeed  Samnani* 9970467440\n"
            "Tushar 7040655500"
        ),
        raw_message=(
            "🏡 Available on Rent 🏡\n"
            "*Wadgoan Sheri*\n"
            "*Konark Splendour*\n"
            "2bhk Fully Furnished, Garden facing, 32K\n"
            "1st September.\n"
            "*Kalyaninagar*\n"
            "*Sovereign*\n"
            "3bhk sf, 42k. Nego.\n"
            "Immediate.\n"
            "*Koregaon Park*\n"
            "*Clover Dale*\n"
            "Studio Apartment Fully Furnished with Huge Terrace\n"
            "25K Possession From 15 August\n"
            " *Amnnora*\n"
            "3bhk with servent room, lavishly furnished with all white goods\n"
            "60k, nego. immidiete. Family & company lease\n"
            "*Offices on Lease*\n"
            "*Viman Nagar*\n"
            "750 Sq.ft Fully Furnished\n"
            "50 Per Sq.ft\n"
            "750 Sq.ft Unfurnish\n"
            "45 Per Sq.ft\n"
            "*Platinum Square*\n"
            "800 Sq.ft Unfurnish 55 Per Sq.ft\n"
            "For further details call\n"
            "S Qube Properties\n"
            "*Sajeed  Samnani* 9970467440\n"
            "Tushar 7040655500"
        ),
    )

    leads = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})

    assert len(leads) == 7
    assert all(lead.values["Type"] == "Seller" for lead in leads)
    assert all(lead.values["Transaction Type"] == "Rent" for lead in leads)
    assert all(lead.values["Contact Number"] == "9970467440" for lead in leads)
    assert all(lead.values["Name"] == "Sajeed Samnani" for lead in leads)
    assert leads[0].values["Location"] == "Wadgaon Sheri"
    assert leads[0].values["Budget_Min"] == 32000
    assert leads[1].values["Location"] == "Kalyani Nagar"
    assert leads[1].values["Budget_Min"] == 42000
    assert leads[2].values["Location"] == "Koregaon Park"
    assert leads[2].values["Property Type"] == "Studio Apartment"
    assert leads[2].values["Budget_Min"] == 25000
    assert leads[3].values["Location"] == "Amanora"
    assert leads[3].values["Budget_Min"] == 60000
    assert [lead.values["Budget_Min"] for lead in leads[4:]] == [50, 45, 55]
    assert all(lead.values["Property Type"] == "Office" for lead in leads[4:])


def test_emoji_are_removed_during_extraction_for_structured_rows():
    location_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["wakad", "Wakad", "wakad", ""],
        ]
    )
    property_map = MappingResolver(
        [
            ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
            ["flat", "Apartment", "flat, apartment", ""],
        ]
    )
    msg = ParsedMessage(
        timestamp=datetime(2026, 5, 1, 10, 0, 0),
        sender="Broker",
        source="WhatsApp Group",
        message="🏡 Need 2 BHK flat in Wakad budget 70-80L 📞 9876543210",
        raw_message="🏡 Need 2 BHK flat in Wakad budget 70-80L 📞 9876543210",
    )

    lead = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})[0]

    assert lead.values["Type"] == "Buyer"
    assert lead.values["Location"] == "Wakad"
    assert lead.values["Property Type"] == "Apartment"
    assert lead.values["BHK"] == 2
    assert lead.values["Budget_Min"] == 7000000
    assert lead.values["Budget_Max"] == 8000000
    assert lead.values["Contact Number"] == "9876543210"
    assert lead.values["Cleaned Message"] == "need 2 bhk flat in wakad budget 70-80l 9876543210"


def test_office_whatsapp_messages_extract_price_area_contact_and_title_name():
    location_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    property_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    export = (
        "[18/04/25, 4:54:21 PM] +91 70202 40634: *LMS FINSWELL VIMAN NAGAR* OFFICE SPACE FOR SALE 630 SQFT PRICE 1 CR *CALL AMRUTA* 9579035245\n"
        "[18/04/25, 4:54:25 PM] +91 70202 40634: *10 BIZ PARK VIMAN NAGAR* OFFICE SPACE FOR RENT 1190 SQFT RENT 76,000/- *CALL AMRUTA* 9579035245\n"
        "[18/04/25, 4:54:28 PM] +91 70202 40634: *LALWANI HOUSE VIMAN NAGAR* OFFICE SPACE FOR RENT 3500 SQFT RENT 3.25 LACS FURNISHED *CALL AMRUTA* 9579035245\n"
    )

    messages = parse_whatsapp_export(export, "WhatsApp Group")
    leads = to_structured(messages, location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})

    assert len(leads) == 3
    assert [lead.values["Transaction Type"] for lead in leads] == ["Sale", "Rent", "Rent"]
    assert [lead.values["Budget_Min"] for lead in leads] == [10000000, 76000, 325000]
    assert [lead.values["Area_Sqft"] for lead in leads] == [630, 1190, 3500]
    assert all(lead.values["Property Type"] == "Office" for lead in leads)
    assert all(lead.values["Contact Number"] == "9579035245" for lead in leads)
    assert all(lead.values["Name"] == "Amruta" for lead in leads)
    assert all(lead.values["Location"] == "Viman Nagar" for lead in leads)


def test_multiline_society_rent_batch_keeps_each_listing_separate():
    location_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    property_map = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    msg = ParsedMessage(
        timestamp=datetime(2025, 4, 18, 16, 54, 33),
        sender="+91 97621 27908",
        source="WhatsApp Group",
        message=(
            "🌱 *1BHK OXY BEAUMONDE SOCIETY*\n"
            "SEMI FURNISHED 22K\n"
            "ALL ALLOWED\n\n"
            "🌱 *1BHK ANAND YOG SOCIETY*\n"
            "UNFURNISHED\n"
            "RENT - 25K\n\n"
            "🌱 *1BHK GANGA NEBULA SOCIETY*\n"
            "FURNISHED\n"
            "RENT - 32K\n\n"
            "🌱 *1BHK BHAKTI ELEGANCE SOCIETY*\n"
            "FURNISHED\n"
            "RENT - 34K\n\n"
            "🌱 *1BHK GREEN OASIS SOCIETY*\n"
            "KALYANI NAGAR\n"
            "FURNISHED\n"
            "RENT - 30K"
        ),
        raw_message=(
            "🌱 *1BHK OXY BEAUMONDE SOCIETY*\n"
            "SEMI FURNISHED 22K\n"
            "ALL ALLOWED\n\n"
            "🌱 *1BHK ANAND YOG SOCIETY*\n"
            "UNFURNISHED\n"
            "RENT - 25K\n\n"
            "🌱 *1BHK GANGA NEBULA SOCIETY*\n"
            "FURNISHED\n"
            "RENT - 32K\n\n"
            "🌱 *1BHK BHAKTI ELEGANCE SOCIETY*\n"
            "FURNISHED\n"
            "RENT - 34K\n\n"
            "🌱 *1BHK GREEN OASIS SOCIETY*\n"
            "KALYANI NAGAR\n"
            "FURNISHED\n"
            "RENT - 30K"
        ),
    )

    leads = to_structured([msg], location_map, property_map, {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1})

    assert len(leads) == 5
    assert [lead.values["Budget_Min"] for lead in leads] == [22000, 25000, 32000, 34000, 30000]
    assert [lead.values["BHK"] for lead in leads] == [1, 1, 1, 1, 1]
    assert [lead.values["Furnishing"] for lead in leads] == [
        "Semi Furnished",
        "Unfurnished",
        "Furnished",
        "Furnished",
        "Furnished",
    ]
    assert [lead.values["Location"] for lead in leads] == [
        "Oxy Beaumonde Society",
        "Anand Yog Society",
        "Ganga Nebula Society",
        "Bhakti Elegance Society",
        "Kalyani Nagar",
    ]
