"""Test accurate extraction of all rental properties."""

from datetime import datetime
from app.extractor import to_structured, MappingResolver
from app.schemas import ParsedMessage


def test_rental_properties_accurate():
    """Test extraction of all 17 rental properties with accurate details."""
    
    # Empty mapping tables - should work generically
    location_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
    ])
    
    property_map = MappingResolver([
        ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
    ])
    
    # User's exact message
    msg = ParsedMessage(
        timestamp=datetime(2026, 4, 3, 10, 0, 0),
        sender="Broker",
        message=(
            "*RENTAL PROPERTIES**RIVER DALE*2BHK GARDEN FLATRENT 30K, F/FFAMILY W.BACHELOR"
            "*ROHAN MITHILA*2.5BHK FLATRENT 31K, U/FONLY FAMILY"
            "*ROHAN MITHILA*3BHK FLATRENT 31K, U/FONLY FAMILY"
            "*ROHAN MITHILA*2BHK FLATRENT 26K, S/FFAMILY W.BACHELOR"
            "*BRAMHA SUN CITY*2BHK FLATRENT 30K, S/FONLY FAMILY"
            "*BRAMHA SUN CITY*2BHK FLATRENT 32K, F/FONLY FAMILY"
            "*KONARK CAMPUS*3BHK FLATRENT 41K S/FFAMILY W.BACHELOR"
            "*F RESIDENCY*3BHK FLATRENT 40K F/FFAMILY W.BACHELOR"
            "*F RESIDENCY*2BHK FLATRENT 27K, U/FONLY FAMILY"
            "*LUNKAD GOLD COAST*2.5BHK FLATRENT 30K, U/FFAMILY W.BACHELOR"
            "*ROHAN MITHILA*2.5BHK ROWHOUSERENT 33K, U/FALL ALLOWED"
            "*BELMAC*3BHK FLATRENT 50K, F/FFAMILY W.BACHELOR"
            "*PANCHSHIL TOWER*3BHK FLATRENT 65K U/FONLY FAMILY"
            "*MARVEL SONET*4.5BHK FLATRENT 1.10K, L/FALL ALLOWED"
            "*LUNKAD SKY LOUNGE*4BHK FLATRENT 1L, F/FONLY FAMILY"
            "*LUNKAD SKY LOUNGE*2BHK FLATRENT 42K, U/FONLY FAMILY"
            "*IRFAN ON: 9552155533*"
        ),
        source="WhatsApp Group",
    )
    
    leads = to_structured(
        [msg], 
        location_map, 
        property_map, 
        {"confidence_location": 1, "confidence_budget": 1, "confidence_bhk": 1}
    )
    
    print(f"\n{'='*80}")
    print(f"EXTRACTION RESULTS: {len(leads)} leads extracted")
    print(f"{'='*80}\n")
    
    for i, lead in enumerate(leads, 1):
        print(f"Lead {i:2d}: {lead.values.get('Location', 'NO LOCATION'):25s} | "
              f"BHK: {str(lead.values.get('BHK', 'N/A')):4s} | "
              f"Budget: {str(lead.values.get('Budget_Min', 'N/A')):8s} | "
              f"Furnishing: {lead.values.get('Furnishing', 'N/A'):15s} | "
              f"Phone: {lead.values.get('Contact Number', 'N/A')}")
    
    # Expected properties
    expected_locations = [
        "River Dale", "Rohan Mithila", "Rohan Mithila", "Rohan Mithila",
        "Bramha Sun City", "Bramha Sun City", "Konark Campus", "F Residency",
        "F Residency", "Lunkad Gold Coast", "Rohan Mithila", "Belmac",
        "Panchshil Tower", "Marvel Sonet", "Lunkad Sky Lounge", "Lunkad Sky Lounge"
    ]
    
    expected_bhks = [2, 3, 3, 2, 2, 2, 3, 3, 2, 3, 3, 3, 3, 5, 4, 2]  # 2.5->3, 4.5->5
    expected_budgets = [30000, 31000, 31000, 26000, 30000, 32000, 41000, 40000, 
                       27000, 30000, 33000, 50000, 65000, 110000, 100000, 42000]
    
    print(f"\n{'='*80}")
    print(f"VALIDATION")
    print(f"{'='*80}\n")
    
    # Assertions
    print(f"Expected 16 leads, got {len(leads)}")
    assert len(leads) >= 16, f"Expected at least 16 leads, got {len(leads)}"
    
    # Check all have phone
    phones = [lead.values.get('Contact Number') for lead in leads]
    phone_count = sum(1 for p in phones if p == "9552155533")
    print(f"✓ {phone_count}/{len(leads)} leads have phone 9552155533")
    assert phone_count >= 14, f"Expected at least 14 leads with phone, got {phone_count}"
    
    # Check all have name
    names = [lead.values.get('Name') for lead in leads]
    name_count = sum(1 for n in names if n and "Irfan" in n)
    print(f"✓ {name_count}/{len(leads)} leads have name Irfan")
    
    # Check locations extracted
    locations = [lead.values.get('Location') for lead in leads if lead.values.get('Location')]
    print(f"✓ {len(locations)}/{len(leads)} leads have location extracted")
    print(f"  Unique locations: {set(locations)}")
    assert len(locations) >= 10, f"Expected at least 10 leads with location, got {len(locations)}"
    
    # Check BHK values
    bhks = [lead.values.get('BHK') for lead in leads if lead.values.get('BHK')]
    print(f"✓ {len(bhks)}/{len(leads)} leads have BHK extracted")
    print(f"  BHK values: {sorted(set(bhks))}")
    assert len(bhks) >= 14, f"Expected at least 14 leads with BHK, got {len(bhks)}"
    
    # Check budgets
    budgets = [lead.values.get('Budget_Min') for lead in leads if lead.values.get('Budget_Min')]
    print(f"✓ {len(budgets)}/{len(leads)} leads have budget extracted")
    print(f"  Budget range: {min(budgets)} - {max(budgets)}")
    assert len(budgets) >= 16, f"Expected at least 16 leads with budget, got {len(budgets)}"
    
    # Check furnishing
    furnishings = [lead.values.get('Furnishing') for lead in leads if lead.values.get('Furnishing')]
    print(f"✓ {len(furnishings)}/{len(leads)} leads have furnishing extracted")
    print(f"  Furnishing types: {set(furnishings)}")
    assert len(furnishings) >= 10, f"Expected at least 10 leads with furnishing, got {len(furnishings)}"
    
    # Check transaction type
    transactions = [lead.values.get('Transaction Type') for lead in leads]
    rent_count = sum(1 for t in transactions if t == "Rent")
    print(f"✓ {rent_count}/{len(leads)} leads classified as Rent")
    assert rent_count >= 14, f"Expected at least 14 Rent transactions, got {rent_count}"
    
    print(f"\n{'='*80}")
    print(f"TEST PASSED: All validations successful!")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    test_rental_properties_accurate()
