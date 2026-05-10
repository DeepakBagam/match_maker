"""
Enhanced location extraction using geocoding services.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache

try:
    from geopy.geocoders import Nominatim
    from geopy.exc import GeocoderTimedOut, GeocoderServiceError
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

# Environment variable to disable geocoding if it's too slow
USE_GEOCODING = os.getenv("USE_GEOCODING", "true").lower() == "true"


class LocationExtractor:
    """Enhanced location extraction with Nominatim geocoding."""
    
    def __init__(self):
        self.nominatim_client = None
        
        if GEOPY_AVAILABLE and USE_GEOCODING:
            # Reduce timeout to 1 second for faster processing
            self.nominatim_client = Nominatim(user_agent="matchlayer_location_extractor", timeout=1)
            print("[INFO] Nominatim geocoding enabled (1s timeout)")
        elif not USE_GEOCODING:
            print("[INFO] Geocoding disabled via USE_GEOCODING=false")
        else:
            print("[INFO] Geopy not available - using built-in location extraction only")
    
    @lru_cache(maxsize=5000)  # Increased cache size
    def extract_location_from_text(self, text: str, city: str = "Pune") -> str | None:
        """Extract location from text using Nominatim geocoding with aggressive caching."""
        if not self.nominatim_client:
            return None
        
        text = text.strip()
        if not text or len(text) < 3 or len(text) > 50:  # Skip very short or very long text
            return None
        
        try:
            # Search for location in the given city
            query = f"{text}, {city}, Maharashtra, India"
            location = self.nominatim_client.geocode(query, exactly_one=True, addressdetails=True)
            
            if location and location.raw.get('address'):
                address = location.raw['address']
                
                # Try to get the most specific location
                for key in ['suburb', 'neighbourhood', 'quarter', 'residential', 'locality']:
                    if key in address:
                        area = address[key]
                        if area.lower() != city.lower():
                            return area
                
                # Fallback to road or other components
                if 'road' in address:
                    return address['road']
        
        except (GeocoderTimedOut, GeocoderServiceError):
            pass  # Timeout or service error - return None quickly
        except Exception:
            pass  # Any other error - gracefully degrade
        
        return None


# Singleton instance
_location_extractor: LocationExtractor | None = None


def get_location_extractor() -> LocationExtractor:
    """Get or create the location extractor singleton."""
    global _location_extractor
    
    if _location_extractor is None:
        _location_extractor = LocationExtractor()
    
    return _location_extractor


def extract_location_enhanced(text: str, city: str = "Pune") -> str | None:
    """Enhanced location extraction using geocoding."""
    extractor = get_location_extractor()
    return extractor.extract_location_from_text(text, city)


if __name__ == "__main__":
    # Test location extraction
    test_messages = [
        "Koregaon Park",
        "Viman Nagar",
        "Magarpatta",
        "Kharadi",
        "Hinjewadi",
    ]
    
    print("Testing location extraction with geocoding:\n")
    for msg in test_messages:
        location = extract_location_enhanced(msg)
        print(f"Input: {msg:20} -> Location: {location}")
