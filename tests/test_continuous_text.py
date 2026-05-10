"""Test continuous text splitting with asterisk delimiters."""

from datetime import datetime
from app.extractor import to_structured, MappingResolver
from app.schemas import ParsedMessage


def test_continuous_text_with_asterisks():
    """Test splitting continuous text with asterisks as delimiters."""
    
    location_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["kalyaninagar", "Kalyani Nagar", "kalyaninagar,kalyani nagar", ""],
        ["wagholi", "Wagholi", "wagholi", ""],
    ])
    
    property_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
        ["shop", "Shop", "shop", ""],
    ])
    
    # Your example message - continuous text with asterisks
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Broker",
        message=(
            "Available on Rent *Kalyaninagar**Kumar Kruti*3bhk, unfurnished, 30k"
            "*Bramha Platinum* *3 n half bhk* semi furnished with wardrobes & AC, 2400sft, 58k nego. Immediate. Avl."
            "*F residency*Shop, Ground floor, 510 sqft, 55k. Immediate."
            "*Available for Sale**F residency*Shop,1st floor, 576 sqft.1.15 cr, negotiable."
            "*Bramha Suncity*2bhk, 1245 sqft, 88L."
            "*Wagholi**Alfa Life scape*3bhk, 1140 sq feet. 50L.With open car park Kesnand Road Wagholi"
            "Key Avl.For further details call*S Qube Properties**Sajeed Samnani* 9970467440Tushar 7040655500"
        ),
        source="WhatsApp Group",
    )
    
    leads = to_structured(
        [msg], 
        location_map, 
        property_map, 
        {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1}
    )
    
    # Should extract multiple leads
    print(f"\\nExtracted {len(leads)} leads:")
    for i, lead in enumerate(leads, 1):
        print(f"\\nLead {i}:")
        print(f"  Type: {lead.values.get('Type')}")
        print(f"  Transaction: {lead.values.get('Transaction Type')}")
        print(f"  Location: {lead.values.get('Location')}")
        print(f"  Property Type: {lead.values.get('Property Type')}")
        print(f"  BHK: {lead.values.get('BHK')}")
        print(f"  Budget: {lead.values.get('Budget_Min')} - {lead.values.get('Budget_Max')}")
        print(f"  Area: {lead.values.get('Area_Sqft')}")
        print(f"  Furnishing: {lead.values.get('Furnishing')}")
        print(f"  Project: {lead.values.get('Project_Name')}")
        print(f"  Phone: {lead.values.get('Contact Number')}")
        print(f"  Name: {lead.values.get('Name')}")
        print(f"  Cleaned: {lead.values.get('Cleaned Message')[:80]}...")
    
    # Assertions
    assert len(leads) >= 5, f"Expected at least 5 leads, got {len(leads)}"
    
    # Check that we extracted different properties
    budgets = [lead.values.get('Budget_Min') for lead in leads if lead.values.get('Budget_Min')]
    assert len(set(budgets)) >= 5, f"Expected at least 5 different budgets, got {len(set(budgets))}: {set(budgets)}"
    
    # Check phone extraction
    phones = [lead.values.get('Contact Number') for lead in leads]
    assert all(phone == "9970467440" for phone in phones), "All leads should have phone 9970467440"
    
    # Check that different property types were extracted
    property_types = [lead.values.get('Property Type') for lead in leads if lead.values.get('Property Type')]
    assert 'Shop' in property_types, "Should extract Shop property type"
    assert 'Apartment' in property_types, "Should extract Apartment property type"
    
    # Check area extraction
    areas = [lead.values.get('Area_Sqft') for lead in leads if lead.values.get('Area_Sqft')]
    assert len(areas) >= 2, f"Should extract area for at least 2 properties, got {len(areas)}"
    
    # Check furnishing extraction
    furnishings = [lead.values.get('Furnishing') for lead in leads if lead.values.get('Furnishing')]
    assert 'Unfurnished' in furnishings, "Should extract Unfurnished"
    assert 'Semi Furnished' in furnishings, "Should extract Semi Furnished"
    
    print(f"\\nTest passed: Extracted {len(leads)} leads from continuous text")
    print(f"Budgets: {sorted(set(budgets))}")
    print(f"Property types: {set(property_types)}")
    print(f"Areas: {areas}")
    print(f"Furnishings: {set(furnishings)}")


if __name__ == "__main__":
    test_continuous_text_with_asterisks()
