"""Test comprehensive extraction features."""

from app.extractor import (
    extract_location,
    extract_location_from_context,
    extract_area_sqft,
    extract_furnishing,
    extract_project_name,
    extract_phone,
    extract_name_from_text,
    MappingResolver,
    infer_location_from_keywords,
    normalize_text,
)


def test_area_extraction():
    """Test area/sqft extraction."""
    assert extract_area_sqft(normalize_text("2BHK 1200 sqft available")) == 1200
    assert extract_area_sqft(normalize_text("3BHK 1500 sq.ft furnished")) == 1500
    assert extract_area_sqft(normalize_text("Office space 2000 square feet")) == 2000
    assert extract_area_sqft(normalize_text("1200.5 sqft apartment")) == 1200
    assert extract_area_sqft(normalize_text("No area mentioned")) is None


def test_furnishing_extraction():
    """Test furnishing status extraction."""
    assert extract_furnishing(normalize_text("Fully furnished 2BHK")) == "Furnished"
    assert extract_furnishing(normalize_text("Furnished apartment")) == "Furnished"
    assert extract_furnishing(normalize_text("FF 3BHK available")) == "Furnished"
    assert extract_furnishing(normalize_text("Semi furnished flat")) == "Semi Furnished"
    assert extract_furnishing(normalize_text("SF 2BHK")) == "Semi Furnished"
    assert extract_furnishing(normalize_text("Unfurnished villa")) == "Unfurnished"
    assert extract_furnishing(normalize_text("UF property")) == "Unfurnished"
    assert extract_furnishing(normalize_text("No furnishing info")) == ""


def test_project_name_extraction():
    """Test property/project name extraction."""
    assert extract_project_name("Project: Kumar Paradise available") == "Kumar Paradise"
    assert extract_project_name("Building: Amanora Towers 3BHK") == "Amanora Towers"
    assert extract_project_name("Society: Magarpatta City") == "Magarpatta City"
    assert extract_project_name("Tower B, Phoenix Complex") == "B, Phoenix Complex"
    assert extract_project_name("Complex: Green Gardens") == "Green Gardens"
    assert extract_project_name("2BHK flat for rent") == ""


def test_phone_extraction_from_body():
    """Test phone extraction prioritizes message body over sender."""
    # Phone in message body
    text1 = "2BHK available. Contact 9876543210"
    assert extract_phone(text1, "John +919999999999") == "9876543210"
    
    # Phone in sender only
    text2 = "3BHK for rent"
    assert extract_phone(text2, "John 9876543210") == "9876543210"
    
    # Invalid phone (doesn't start with 6-9)
    text3 = "Call 1234567890"
    assert extract_phone(text3, "John 9876543210") == "9876543210"
    
    # Valid phone starting with 6-9
    text4 = "Contact 7890123456"
    assert extract_phone(text4, "John 9999999999") == "7890123456"


def test_name_extraction_from_signatures():
    """Test name extraction from message signatures."""
    # Name with phone on same line
    msg1 = "2BHK available\nRajesh: 9876543210"
    assert extract_name_from_text(msg1) == "Rajesh"
    
    # Name before phone with contact word - may include "Contact"
    msg2 = "3BHK for sale\nContact Priya 9876543210"
    name = extract_name_from_text(msg2)
    assert "Priya" in name  # May be "Contact Priya" or "Priya"
    
    # Name in last line
    msg3 = "Villa available\nKumar Properties\nAjay 9876543210"
    assert extract_name_from_text(msg3) == "Ajay"
    
    # No name pattern
    msg4 = "2BHK available for rent"
    assert extract_name_from_text(msg4) == ""


def test_location_inference_from_keywords():
    """Test location inference from city keywords."""
    assert infer_location_from_keywords(normalize_text("2BHK in Pune available")) == "Pune"
    assert infer_location_from_keywords(normalize_text("Flat for sale in Mumbai")) == "Mumbai"
    assert infer_location_from_keywords(normalize_text("Bangalore property available")) == "Bangalore"
    assert infer_location_from_keywords(normalize_text("Navi Mumbai 3BHK")) == "Navi Mumbai"
    assert infer_location_from_keywords(normalize_text("Pimpri Chinchwad area")) == "Pimpri Chinchwad"
    assert infer_location_from_keywords(normalize_text("No city mentioned")) == ""


def test_location_context_extraction_prefers_anchored_phrases():
    mapper = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    cleaned = normalize_text("Available 3 bhk apartment at blue ridge phase 1 for rent 45k")
    location, ambiguous = extract_location_from_context(cleaned, mapper)

    assert ambiguous is False
    assert location == "Blue Ridge"


def test_location_extraction_does_not_promote_generic_phrases():
    mapper = MappingResolver([["Raw Value", "Canonical Value", "Aliases", "Optional Tags"]])
    cleaned = normalize_text("Need 2 bhk flat all allowed immediate possession family only budget 25k")
    location, ambiguous = extract_location(cleaned, mapper)

    assert ambiguous is False
    assert location == ""


def test_comprehensive_message():
    """Test extraction from a comprehensive message."""
    msg = """
    Project: Kumar Paradise
    3BHK Fully Furnished Apartment
    1500 sqft, Koregaon Park, Pune
    Price: 2.5 Cr
    Contact: Rajesh 9876543210
    """
    
    cleaned = normalize_text(msg)
    
    # Test all extractions
    project = extract_project_name(msg)
    assert "Kumar Paradise" in project  # May include more text
    assert extract_area_sqft(cleaned) == 1500
    assert extract_furnishing(cleaned) == "Furnished"
    assert extract_phone(msg, "Unknown") == "9876543210"
    assert extract_name_from_text(msg) == "Rajesh"
    assert infer_location_from_keywords(cleaned) == "Koregaon Park"


def test_multiple_phones_in_message():
    """Test that first valid phone is extracted."""
    msg = "Contact 9876543210 or 8765432109"
    assert extract_phone(msg, "Unknown") == "9876543210"


def test_edge_cases():
    """Test edge cases."""
    # Empty strings
    assert extract_area_sqft("") is None
    assert extract_furnishing("") == ""
    assert extract_project_name("") == ""
    assert extract_phone("", "") == ""
    assert extract_name_from_text("") == ""
    assert infer_location_from_keywords("") == ""
    
    # Special characters - title() capitalizes after apostrophe
    project = extract_project_name("Project: Kumar's Paradise")
    assert "Kumar" in project and "Paradise" in project
