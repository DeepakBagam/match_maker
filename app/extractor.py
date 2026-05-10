from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .analytics import budget_bucket, month_bucket, week_bucket
from .datetime_utils import parse_datetime
from .schemas import ParsedMessage, StructuredLead

USE_GEOCODING = os.getenv("USE_GEOCODING", "false").lower() == "true"

BUYER_WORDS = {"want", "need", "required", "requirement", "looking", "buy", "purchase"}
SELLER_WORDS = {"sale", "sell", "available", "avl", "owner", "resale", "listing", "rent", "lease", "rental"}
IGNORE_WORDS = {"good morning", "joke", "congrats", "test", "omitted", "end-to-end encrypted"}
SYSTEM_MESSAGE_PARTS = {
    "left",
    "joined using this group's invite link",
    "joined using group invite link",
    "added you",
    "created this group",
    "changed the group description",
    "changed this group's icon",
    "changed the subject",
    "deleted this message",
}
BUYER_PHRASES = ("wanted", "requirement", "required", "looking for", "urgent required", "urgently required", "need on rent")
SELLER_PHRASES = ("available on rent", "available for rent", "for sale", "on rent", "available", "avl", "rental property", "avl rental")

URGENCY_WORDS = {"urgent", "immediate", "asap", "today", "final"}

PHONE_RE = re.compile(r"(?:\+?91[-\s]?)?(\d{10})")
BULLET_RE = re.compile(r"^[\W_]*")
BHK_LINE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*[- ]?bhk\b", re.IGNORECASE)
PRICE_TOKEN_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:cr|crore|l|lac|lakh|k|thousand)\b", re.IGNORECASE)
LINE_PHONE_RE = re.compile(r"^\s*([a-z][a-z\s]{1,40})\s*[:\-]?\s*(?:\+?91[-\s]?)?(\d{10})\s*$", re.IGNORECASE)
CONTACT_NAME_RE = re.compile(r"\b(?:call|contact|reach|whatsapp|wa)\s+([a-z][a-z\s]{1,40}?)(?=\s+(?:\+?91[-\s]?)?\d{10}\b|$)", re.IGNORECASE)
BHK_RE = re.compile(r"(?<![\d.])(\d+(?:\.5)?)\s*[- ]?bhk\b", re.IGNORECASE)
AMBIGUOUS_BHK_RE = re.compile(r"\b(?:\d+\s+\d+\s*[- ]?bhk|1\s*[- ]?rk|studio)\b", re.IGNORECASE)
RANGE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(cr|crore|crores|l|la|lac|lacs|lakh|lakhs|k|thousand)?\s*(?:-|to)\s*(\d[\d,]*(?:\.\d+)?)\s*(cr|crore|crores|l|la|lac|lacs|lakh|lakhs|k|thousand)?", re.IGNORECASE)
SINGLE_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s*(?:/-)?\s*(cr|crore|crores|l|la|lac|lacs|lakh|lakhs|k|thousand)?\b", re.IGNORECASE)
PLACEHOLDER_BUDGET_RE = re.compile(r"\b(?:apm|apmr|budget market|market|as per market|negotiable|nego|tbd)\b", re.IGNORECASE)
BUDGET_CONTEXT_RE = re.compile(
    r"(?:budget(?:\s*range)?|expecting rent|rent(?:al)?|price|deposit|outright|sale|asking|quote)\s*[:\-]?\s*(?:rs\.?\s*)?([^\n|]{0,60})",
    re.IGNORECASE,
)
PROPERTY_HINTS_RE = re.compile(r"\b(flat|apartment|villa|bungalow|plot|shop|showroom|office|warehouse|godown|commercial|comm|furnished|ff|semi furnished|sf|unfurnished)\b", re.IGNORECASE)
RENT_WORD_RE = re.compile(r"\b(rent|rental|lease|tenant)\b", re.IGNORECASE)
SALE_WORD_RE = re.compile(r"\b(sale|sell|resale|purchase|buy|owner sale)\b", re.IGNORECASE)
LOCATION_WORD_RE = re.compile(r"^[a-z][a-z\s.&/-]{2,40}$", re.IGNORECASE)
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:sq\.?\s*ft|sqft|square\s*feet)\b", re.IGNORECASE)
RATE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:per\s*(?:sq\.?\s*ft|sqft)|psf)\b", re.IGNORECASE)
RATE_CAPTURE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:per\s*(?:sq\.?\s*ft|sqft)|psf)\b", re.IGNORECASE)
STUDIO_RE = re.compile(r"\bstudio\s+apartment\b", re.IGNORECASE)
PROPERTY_SECTION_RE = re.compile(r"\b(offices?|office|shop|showroom|commercial|apartment|flat|villa|plot)\b", re.IGNORECASE)
DETAIL_WORD_RE = re.compile(
    r"\b(furnished|unfurnish|semi furnished|garden facing|terrace|white goods|family|lease|immediate|possession|servant room|servant|company)\b",
    re.IGNORECASE,
)
NOISE_LINE_RE = re.compile(r"\b(for further details|call\b|pls\b|please\b|contact\b|pics on demand|with me)\b", re.IGNORECASE)
BUSINESS_LINE_RE = re.compile(r"\b(properties|realty|estate|associates|broker|realtor)\b", re.IGNORECASE)
FURNISHING_RE = re.compile(r"\b(fully\s+furnished|furnished|semi\s+furnished|semi-furnished|unfurnished)\b|([fusl])\s*/\s*([fusl])(?=[a-z]|\s|,|$)|\b(ff|sf|uf|lf)\b", re.IGNORECASE)
PROJECT_RE = re.compile(r"\b(?:project|building|society|tower|complex|residency|heights|enclave|paradise|gardens?)\s*[:\-]?\s*([a-z][a-z0-9\s&.',-]{2,40})(?=\s*(?:\n|$|\d+\s*bhk))", re.IGNORECASE)
INLINE_PROJECT_RE = re.compile(r"(?:^|\*)\s*(?:\d+(?:\.\d+)?\s*[- ]?bhk\s+)?([A-Za-z][A-Za-z0-9\s&.',/-]{3,50}?(?:society|square|park|house|tower|towers|plaza|campus|residency|residencies|heights|enclave|garden|gardens|court|dale|oasis|beaumonde|nebula|elegance|mithila|sonet|belmac|biz park|finswell))\s*(?=\*|\s+(?:office|flat|apartment|villa|rowhouse|rent|price|sale|semi|fully|furnished|unfurnished|all allowed|family|only|call|contact|$))", re.IGNORECASE)
LOCATION_STOP_PHRASES_RE = re.compile(r"\b(all allowed|family only|only family|working bachelor|bachelor|company lease|immediate|possession|semi furnished|fully furnished|furnished|unfurnished|lavishly furnished|rent|price|sale)\b", re.IGNORECASE)
LOCATION_CONTEXT_RE = re.compile(
    r"\b(?:in|at|near|off|behind|opp(?:osite)?|location|loc(?:ation)?|area)\s+([a-z][a-z\s.&/'-]{2,40}?)(?=\s*(?:,|/|$|\b(?:for|on|rent|sale|lease|price|budget|deposit|sqft|square|bhk|flat|apartment|villa|office|shop|showroom|commercial|furnished|unfurnished|semi|family|bachelor|call|contact|phase|tower|society|building|project)\b))",
    re.IGNORECASE,
)
LOCATION_TRAILING_JUNK_RE = re.compile(
    r"\b(?:for|on|rent|sale|lease|price|budget|deposit|sqft|square|bhk|flat|apartment|villa|office|shop|showroom|commercial|furnished|unfurnished|semi|family|bachelor|call|contact|available|nego|negotiable|only)\b.*$",
    re.IGNORECASE,
)
LOCATION_GENERIC_WORDS = {
    "need", "want", "looking", "required", "requirement", "available", "property", "properties",
    "flat", "apartment", "office", "shop", "showroom", "commercial", "budget", "rent", "sale",
    "lease", "furnished", "unfurnished", "semi", "family", "bachelor", "immediate", "possession",
    "call", "contact", "details", "pics", "allowed", "working", "garden", "facing", "floor",
}
CITY_KEYWORDS = {"pune", "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai", "kolkata", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam", "pimpri", "chinchwad", "patna", "vadodara", "ghaziabad", "ludhiana", "agra", "nashik", "faridabad", "meerut", "rajkot", "kalyan", "dombivli", "vasai", "virar", "varanasi", "srinagar", "aurangabad", "dhanbad", "amritsar", "navi mumbai", "allahabad", "prayagraj", "ranchi", "howrah", "coimbatore", "jabalpur", "gwalior", "vijayawada", "jodhpur", "madurai", "raipur", "kota", "guwahati", "chandigarh", "solapur", "hubli", "dharwad", "mysore", "mysuru", "bareilly", "moradabad", "gurgaon", "gurugram", "aligarh", "jalandhar", "noida", "greater noida"}

# Area abbreviations commonly used in real estate messages
AREA_ABBREVIATIONS = {
    # East Pune
    "kp": "Koregaon Park",
    "kn": "Kalyani Nagar",
    "vn": "Viman Nagar",
    "khrd": "Kharadi",
    "mp": "Magarpatta",
    "m'patta": "Magarpatta",
    "mpatta": "Magarpatta",
    "hdpsr": "Hadapsar",
    "wghl": "Wagholi",
    "yrw": "Yerwada",
    
    # Central Pune
    "sn": "Shivaji Nagar",
    "sb rd": "Shivaji Nagar",
    "fc rd": "Fergusson College Road",
    "jm rd": "Jangali Maharaj Road",
    "mg rd": "MG Road",
    "dp rd": "Dhole Patil Road",
    
    # West Pune
    "bc rd": "Boat Club Road",
    "b c rd": "Boat Club Road",
    
    # PCMC
    "hn": "Hinjewadi",
    "hjw": "Hinjewadi",
    "ps": "Pimple Saudagar",
    "pg": "Pimple Gurav",
    "wb": "Wakad Baner",
    
    # South Pune
    "sg rd": "Sinhagad Road",
}

# Comprehensive location keywords for Pune
PUNE_LOCATIONS = {
    # East Pune
    "viman nagar", "vimanagar", "viman ngr", "viman nagr", "viman",
    "kalyani nagar", "kalyaninagar", "kalyani ngr", "kalyani nagr",
    "koregaon park", "koregoan park", "koregoaw park", "koregaon prk", "koregaon",
    "kharadi", "eon kharadi",
    "wadgaon sheri", "wadgoan sheri", "w sheri",
    "magarpatta", "magarpatta city", "magarpatta cty",
    "hadapsar", "hadapsar area",
    "mundhwa", "mundhva",
    "wagholi",
    "lohegaon", "lohgaon",
    "dhanori",
    "yerwada", "yerawada",
    
    # Central Pune
    "bund garden", "bund garden road", "bund grd",
    "camp", "cantonment",
    "mg road", "m g road",
    "fc road", "f c road", "fergusson college road",
    "jm road", "j m road", "jangali maharaj road",
    "deccan", "deccan gymkhana",
    "rasta peth",
    "shivaji nagar", "shivajinagar",
    "karve nagar", "karvenagar",
    
    # West Pune
    "kothrud", "kothrud area",
    "warje", "warje malwadi",
    "bavdhan",
    "aundh",
    "pashan",
    "baner",
    "balewadi", "balewadi high street",
    "boat club road",
    
    # PCMC Area
    "hinjewadi", "hinjawadi", "rajiv gandhi infotech park",
    "wakad",
    "pimple saudagar",
    "pimple gurav",
    "chinchwad", "pimpri chinchwad",
    "nigdi",
    "akurdi",
    "bhosari",
    "ravet",
    
    # South Pune
    "kondhwa",
    "nibm", "nibm road", "nibm rd",
    "undri",
    "pisoli",
    "wanowrie", "wanorie", "wanowari",
    "fatima nagar",
    "salunke vihar", "salunkvihar", "salunkvhar",
    "lullanagar", "lulla nagar",
    
    # Market Areas
    "market yard",
    "swargate",
    "sinhagad road",
    "satara road",
    "nagar road", "ahmednagar road",
    
    # Landmarks
    "eon", "eon it park",
    "phoenix mall", "phoenix market city",
    "aga khan palace", "agakhan palace",
}

EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)


@dataclass
class MappingEntry:
    canonical: str
    aliases: list[str]


@dataclass
class ListingContext:
    section_header: str = ""
    transaction: str = ""
    location: str = ""
    project: str = ""
    property_context: str = ""


@dataclass
class ListingBuffer:
    context: ListingContext
    lines: list[str]


class MappingResolver:
    def __init__(self, rows: list[list[str]]):
        self.entries: list[MappingEntry] = []
        for row in rows[1:]:
            if len(row) < 2:
                continue
            canonical = (row[1] or row[0]).strip()
            aliases_raw = row[2] if len(row) > 2 else ""
            tags_raw = row[3] if len(row) > 3 else ""
            aliases = [a.strip().lower() for a in f"{aliases_raw},{tags_raw}".split(",") if a.strip()]
            raw = (row[0] or "").strip().lower()
            if raw:
                aliases.append(raw)
            if canonical:
                self.entries.append(MappingEntry(canonical=canonical, aliases=list(dict.fromkeys(aliases))))

    def resolve(self, text: str) -> tuple[str, bool]:
        candidate = _mapping_normalize(text)
        matches: list[tuple[str, int]] = []
        for entry in self.entries:
            best_for_entry = 0
            for alias in entry.aliases:
                normalized_alias = _mapping_normalize(alias)
                if not normalized_alias:
                    continue
                if re.search(rf"(?<!\w){re.escape(normalized_alias)}(?!\w)", candidate):
                    best_for_entry = max(best_for_entry, len(normalized_alias))
            if best_for_entry:
                matches.append((entry.canonical, best_for_entry))
        if not matches:
            return "", False
        best_len = max(length for _, length in matches)
        unique = sorted({canonical for canonical, length in matches if length == best_len})
        if len(unique) == 1:
            return unique[0], False
        if len(unique) > 1:
            return "", True
        return "", False


def normalize_text(raw: str) -> str:
    """Normalize text while preserving key information."""
    # Remove emojis
    without_emoji = EMOJI_RE.sub(" ", raw)
    
    # Remove markdown formatting but keep text
    without_emoji = without_emoji.replace("*", "")
    
    # Convert to lowercase
    text = without_emoji.lower()
    
    # Remove special chars but keep: letters, numbers, spaces, commas, hyphens, slashes, DOTS
    text = re.sub(r"[^a-z0-9\s,\-/.]", " ", text)
    
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    
    return text


def _mapping_normalize(raw: str) -> str:
    cleaned = normalize_text(raw)
    cleaned = cleaned.replace("society", "soc")
    cleaned = re.sub(r"\bco[\s-]?op\b", "coop", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def extract_name(sender: str) -> str:
    name = PHONE_RE.sub("", sender)
    name = re.sub(r"\s+", " ", name).strip(" -:+")
    if not name or not re.search(r"[a-zA-Z]", name):
        return ""
    return name.title()


def extract_name_from_text(text: str) -> str:
    """Extract person name from message signatures."""
    lines = text.splitlines()
    
    candidates: list[str] = []

    # Check last 5 lines for name patterns, keeping original order so the
    # primary broker/contact line wins over trailing secondary numbers.
    for line in lines[-5:]:
        line = re.sub(r"\s+", " ", line.replace("*", " ")).strip()
        match = LINE_PHONE_RE.match(line)
        if match:
            candidates.append(re.sub(r"\s+", " ", match.group(1)).strip().title())
            continue

        contact_match = CONTACT_NAME_RE.search(line)
        if contact_match:
            candidates.append(re.sub(r"\s+", " ", contact_match.group(1)).strip(" -*:").title())
            continue
        
        # Pattern: Name followed by phone on same line
        if PHONE_RE.search(line):
            # Extract name before phone (remove phone and common words)
            name_part = PHONE_RE.sub("", line)
            # Remove common contact words more aggressively
            name_part = re.sub(r'^\s*(?:contact|call|reach|whatsapp|wa)\s*[:\-]?\s*', '', name_part, flags=re.IGNORECASE)
            name_part = name_part.strip(" -*:,")
            if name_part and 2 <= len(name_part) <= 40 and not any(ch.isdigit() for ch in name_part):
                # Check if it looks like a name (not a sentence)
                words = name_part.split()
                if len(words) <= 3:
                    candidates.append(re.sub(r"\s+", " ", name_part).title())

    return candidates[0] if candidates else ""


def classify_message(cleaned: str) -> str:
    """Classify message as Buyer, Seller, or Ignore."""
    inventory_words = {"available", "avl", "owner", "resale", "listing", "sell"}
    buyer_intent_words = {"want", "need", "required", "requirement", "looking", "buy", "purchase"}

    # Check for ignore patterns first
    if any(w in cleaned for w in IGNORE_WORDS):
        return "Ignore"
    if any(part in cleaned for part in SYSTEM_MESSAGE_PARTS):
        return "Ignore"
    if cleaned.endswith(" left") or cleaned.endswith(" joined"):
        return "Ignore"
    
    # Check for explicit buyer/seller phrases FIRST (higher priority)
    if any(phrase in cleaned for phrase in BUYER_PHRASES):
        return "Buyer"
    
    if any(phrase in cleaned for phrase in SELLER_PHRASES):
        return "Seller"

    # Buyer intent plus transaction words like "sale" or "rent" should still stay Buyer
    if any(word in cleaned for word in buyer_intent_words) and not any(word in cleaned for word in inventory_words):
        return "Buyer"
    
    # Word-based classification (before BHK+price check)
    buyer_hits = sum(1 for w in BUYER_WORDS if w in cleaned)
    seller_hits = sum(1 for w in SELLER_WORDS if w in cleaned)
    
    if buyer_hits > seller_hits:
        return "Buyer"
    elif seller_hits > buyer_hits:
        return "Seller"
    
    # Only use BHK+price as fallback when no clear buyer/seller signals
    has_bhk = bool(BHK_RE.search(cleaned) or re.search(r'\d+\s*bhk', cleaned, re.IGNORECASE))
    has_price = bool(PRICE_TOKEN_RE.search(cleaned) or re.search(r'\d+k|\d+lac|\d+cr', cleaned, re.IGNORECASE))
    if has_bhk and has_price:
        return "Seller"
    
    # If has property details but no clear classification
    if has_bhk or has_price:
        return "Seller"
    if SIZE_RE.search(cleaned) or RATE_RE.search(cleaned) or PROPERTY_SECTION_RE.search(cleaned):
        return "Seller"
    
    return "Ignore"


def _parse_amount(num: float, unit: str | None) -> int:
    """Parse amount with unit to integer value."""
    if not unit:
        return int(num)
    unit = unit.lower()
    if unit in {"cr", "crore", "crores"}:
        return int(num * 10_000_000)
    if unit in {"l", "lac", "lacs", "lakh", "lakhs", "la"}:
        return int(num * 100_000)
    if unit in {"k", "thousand"}:
        return int(num * 1_000)
    return int(num)


def _extract_amount(raw_number: str, unit: str | None) -> int:
    number = float(raw_number.replace(",", ""))
    value = _parse_amount(number, unit)
    # Real-estate broker chats sometimes use "1.10K" for "1.10 lakh".
    if unit and unit.lower() == "k" and "." in raw_number and value < 10_000:
        return int(number * 100_000)
    return value


def _sanitize_budget_text(text: str) -> str:
    without_phones = PHONE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", without_phones).strip()


def _is_budget_candidate(raw_number: str, unit: str | None, value: int) -> bool:
    digits_only = raw_number.replace(".", "").replace(",", "")
    if unit:
        return True
    if len(digits_only) >= 10:
        return False
    return value >= 10_000


def extract_budget(cleaned: str) -> tuple[int | None, int | None, bool]:
    sanitized = _sanitize_budget_text(cleaned)
    
    # Check for explicit budget context first
    for context in BUDGET_CONTEXT_RE.finditer(sanitized):
        snippet = context.group(1)
        if PLACEHOLDER_BUDGET_RE.search(snippet):
            continue
        range_match = RANGE_RE.search(snippet)
        if range_match:
            low_unit = range_match.group(2) or range_match.group(4)
            high_unit = range_match.group(4) or range_match.group(2)
            low = _extract_amount(range_match.group(1), low_unit)
            high = _extract_amount(range_match.group(3), high_unit)
            return (min(low, high), max(low, high), False)

        singles = []
        for match in SINGLE_RE.finditer(snippet):
            unit = match.group(2)
            value = _extract_amount(match.group(1), unit)
            if _is_budget_candidate(match.group(1), unit, value):
                singles.append(value)
        if singles:
            amt = singles[0]
            return amt, amt, True

    # Remove placeholder text
    if PLACEHOLDER_BUDGET_RE.search(sanitized):
        sanitized = PLACEHOLDER_BUDGET_RE.sub(" ", sanitized)
    
    # Check for rate per sqft
    rate_match = RATE_CAPTURE_RE.search(sanitized)
    if rate_match:
        rate_value = int(float(rate_match.group(1)))
        return rate_value, rate_value, True
    
    # Look for range patterns anywhere
    range_match = RANGE_RE.search(sanitized)
    if range_match:
        low_unit = range_match.group(2) or range_match.group(4)
        high_unit = range_match.group(4) or range_match.group(2)
        low = _extract_amount(range_match.group(1), low_unit)
        high = _extract_amount(range_match.group(3), high_unit)
        return (min(low, high), max(low, high), False)

    # Collect all valid budget candidates
    candidates: list[int] = []
    for match in SINGLE_RE.finditer(sanitized):
        value = _extract_amount(match.group(1), match.group(2))
        unit = match.group(2)
        if _is_budget_candidate(match.group(1), unit, value):
            candidates.append(value)
    
    # Take first valid candidate
    if candidates:
        amt = candidates[0]
        return amt, amt, True

    return None, None, False


def extract_bhk(cleaned: str) -> tuple[int | None, bool]:
    """Extract BHK with support for decimal values like 2.5bhk, 3.5bhk, 4.5bhk."""
    # Check for ambiguous patterns first
    if re.search(r'\b(?:\d+\s+\d+\s*[- ]?bhk|1\s*[- ]?rk|studio)\b', cleaned, re.IGNORECASE):
        return None, True
    
    # Extract BHK including decimals (2.5, 3.5, 4.5)
    hit = BHK_RE.search(cleaned)
    if hit:
        bhk_value = float(hit.group(1))
        # Round up for .5 values: 2.5 -> 3, 3.5 -> 4, 4.5 -> 5
        return int(bhk_value + 0.5), False
    
    return None, False


def extract_transaction_type(cleaned: str, budget_min: int | None = None, budget_max: int | None = None) -> str:
    """Extract transaction type with inference from budget."""
    # Explicit keywords first
    if RENT_WORD_RE.search(cleaned):
        return "Rent"
    if SALE_WORD_RE.search(cleaned):
        return "Sale"
    
    # Check for deposit/advance keywords (indicates rent)
    if re.search(r'\b(deposit|advance|security|pm|per month|monthly)\b', cleaned, re.IGNORECASE):
        return "Rent"
    
    # Infer from budget if available (more aggressive)
    if budget_min is not None and budget_max is not None:
        avg_budget = (budget_min + budget_max) / 2
        # Rent is typically < 3L per month, Sale is > 10L
        if avg_budget < 300_000:
            return "Rent"
    
    return ""


def extract_phone(text: str, sender: str) -> str:
    """Extract phone number from message body first, then sender."""
    # Try message body first
    for match in PHONE_RE.finditer(text):
        phone = match.group(1)
        # Validate it's not part of a larger number
        if len(phone) == 10 and phone[0] in '6789':
            return phone
    
    # Fallback to sender
    sender_hit = PHONE_RE.search(sender)
    if sender_hit:
        return sender_hit.group(1)
    return ""


def extract_property_hint(cleaned: str) -> str:
    hits = [match.group(1).lower() for match in PROPERTY_HINTS_RE.finditer(cleaned)]
    unique = list(dict.fromkeys(hits))
    for preferred in ("office", "commercial", "shop", "showroom", "warehouse", "godown", "villa", "bungalow", "plot", "flat", "apartment"):
        if preferred in unique:
            return preferred
    if len(unique) == 1:
        return unique[0]
    return ""


def extract_area_sqft(cleaned: str) -> int | None:
    """Extract area in square feet."""
    match = SIZE_RE.search(cleaned)
    if match:
        return int(float(match.group(1)))
    return None


def extract_furnishing(cleaned: str) -> str:
    """Extract furnishing status from patterns like F/F, S/F, U/F, L/F."""
    match = FURNISHING_RE.search(cleaned)
    if not match:
        return ""
    
    # Check if it's a word match or abbreviation match
    if match.group(1):
        furnish = match.group(1).lower()
        if furnish in {"fully furnished", "furnished"}:
            return "Furnished"
        elif furnish in {"semi furnished", "semi-furnished"}:
            return "Semi Furnished"
        elif furnish == "unfurnished":
            return "Unfurnished"
    elif match.group(2) and match.group(3):
        # Abbreviation like f/f, s/f, u/f, l/f
        abbr = f"{match.group(2)}/{match.group(3)}".lower()
        if abbr == "f/f":
            return "Furnished"
        elif abbr == "s/f":
            return "Semi Furnished"
        elif abbr == "u/f":
            return "Unfurnished"
        elif abbr == "l/f":
            return "Lavishly Furnished"
    elif match.group(4):
        abbr = match.group(4).lower()
        if abbr == "ff":
            return "Furnished"
        elif abbr == "sf":
            return "Semi Furnished"
        elif abbr == "uf":
            return "Unfurnished"
        elif abbr == "lf":
            return "Lavishly Furnished"
    
    return ""


def extract_project_name(text: str) -> str:
    """Extract property/project name."""
    match = PROJECT_RE.search(text)
    if match:
        name = match.group(1).strip()
        # Remove trailing words that are property details (more aggressive)
        name = re.sub(r'\s+\d+\s*bhk.*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+(?:available|for|sale|rent|apartment|flat|villa).*$', '', name, flags=re.IGNORECASE)
        name = name.strip()
        if not re.search(r"\b(rent|price|furnished|unfurnished|semi furnished|all allowed)\b", name, re.IGNORECASE):
            return name.title()
    inline_match = INLINE_PROJECT_RE.search(text)
    if inline_match:
        name = inline_match.group(1).strip(" -*,.:")
        if not LOCATION_STOP_PHRASES_RE.search(name):
            return name.title()
    return ""


def infer_location_from_keywords(cleaned: str) -> str:
    """Infer location from known Pune area keywords."""
    text_lower = cleaned.lower()
    
    # Check for multi-word locations first (more specific)
    multi_word_locs = sorted([loc for loc in PUNE_LOCATIONS if ' ' in loc], key=len, reverse=True)
    for loc in multi_word_locs:
        if loc in text_lower:
            return loc.title()
    
    # Check for single-word locations
    words = text_lower.split()
    for word in words:
        if word in PUNE_LOCATIONS:
            return word.title()
    
    # Fallback to city keywords
    for city in CITY_KEYWORDS:
        if ' ' in city and city in text_lower:
            return city.title()
    
    for word in words:
        if word in CITY_KEYWORDS:
            return word.title()
    
    return ""


def _clean_location_candidate(candidate: str) -> str:
    candidate = candidate.strip(" ,./:-")
    candidate = re.sub(r"\b(?:furnished|unfurnished|semi furnished|fully furnished|lavishly furnished)\b.*$", "", candidate, flags=re.IGNORECASE)
    candidate = LOCATION_TRAILING_JUNK_RE.sub("", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,./:-")
    return candidate


def _is_valid_location_candidate(candidate: str) -> bool:
    if not candidate:
        return False
    lowered = candidate.lower().strip()
    if any(ch.isdigit() for ch in lowered):
        return False
    if len(lowered.split()) > 5:
        return False
    if LOCATION_STOP_PHRASES_RE.search(lowered):
        return False
    if NOISE_LINE_RE.search(lowered) or BUSINESS_LINE_RE.search(lowered):
        return False
    words = [word for word in re.split(r"[\s/&.-]+", lowered) if word]
    if not words:
        return False
    if all(word in LOCATION_GENERIC_WORDS for word in words):
        return False
    return bool(LOCATION_WORD_RE.match(lowered))


def extract_location_from_context(cleaned: str, location_mapper: MappingResolver) -> tuple[str, bool]:
    candidates: list[str] = []
    for match in LOCATION_CONTEXT_RE.finditer(cleaned):
        candidate = _clean_location_candidate(match.group(1))
        if _is_valid_location_candidate(candidate):
            candidates.append(candidate)

    for candidate in candidates:
        resolved, ambiguous = location_mapper.resolve(candidate)
        if resolved or ambiguous:
            return resolved, ambiguous

    for candidate in candidates:
        inferred = infer_location_from_keywords(candidate)
        if inferred:
            return inferred, False

    if candidates:
        return candidates[0].title(), False

    return "", False


def expand_area_abbreviation(text: str) -> str:
    """Expand area abbreviations like KP, KN, SN to full names."""
    text_lower = text.lower()
    
    # Check for abbreviations with word boundaries
    for abbr, full_name in AREA_ABBREVIATIONS.items():
        # Match abbreviation as standalone word or after space
        pattern = rf'\b{re.escape(abbr)}\b'
        if re.search(pattern, text_lower):
            return full_name
    
    return ""


def infer_property_type(bhk: int | None, budget_min: int | None, transaction: str, property_hint: str, has_mapping_table: bool = True) -> str:
    """Infer property type from BHK, budget, and hints.
    
    Args:
        has_mapping_table: If True, only infer when hint is available. If False, infer more aggressively.
    """
    # Use hint if available
    if property_hint:
        hint_lower = property_hint.lower()
        if hint_lower in {"flat", "apartment", "furnished", "ff", "sf", "unfurnished"}:
            return "Apartment"
        elif hint_lower in {"villa", "bungalow"}:
            return "Villa"
        elif hint_lower in {"office", "commercial", "comm"}:
            return "Office"
        elif hint_lower in {"shop", "showroom"}:
            return "Shop"
        elif hint_lower in {"plot"}:
            return "Plot"
    
    # Only infer from BHK/budget if no mapping table exists
    if not has_mapping_table and bhk and budget_min:
        # Office/Commercial: Low rent, no BHK or small BHK
        if transaction == "Rent" and budget_min < 100_000 and bhk <= 2:
            return "Office"
        
        # Villa: High budget (> 5Cr) or large BHK (4+)
        if budget_min > 50_000_000 or bhk >= 4:
            return "Villa"
        
        # Apartment: Default for residential with BHK
        if bhk >= 1:
            return "Apartment"
    
    return ""


def _looks_like_location(line: str) -> bool:
    if not line or PHONE_RE.search(line) or any(ch.isdigit() for ch in line):
        return False
    lowered = line.lower()
    if NOISE_LINE_RE.search(lowered) or BUSINESS_LINE_RE.search(lowered):
        return False
    if LOCATION_STOP_PHRASES_RE.search(lowered):
        return False
    if RENT_WORD_RE.search(lowered) or SALE_WORD_RE.search(lowered):
        return False
    if PROPERTY_SECTION_RE.search(lowered):
        return False
    return bool(LOCATION_WORD_RE.match(lowered))


def _listing_signal_score(line: str) -> int:
    """Calculate how likely a line is a property listing."""
    score = 0
    if BHK_LINE_RE.search(line):
        score += 4
    if STUDIO_RE.search(line):
        score += 4
    if SIZE_RE.search(line):
        score += 3
    if PRICE_TOKEN_RE.search(line):
        score += 3
    if RATE_RE.search(line):
        score += 3
    # Check for price patterns like "28000/-" or "60000/-"
    if re.search(r'\d{4,}/-', line):
        score += 3
    if DETAIL_WORD_RE.search(line):
        score += 1
    if PROPERTY_SECTION_RE.search(line):
        score += 1
    return score


def _classify_line_role(line: str) -> str:
    """Classify the role of a line in a bulk message."""
    lowered = line.lower()
    if not line:
        return "empty"
    if LINE_PHONE_RE.match(line) or PHONE_RE.search(line):
        return "contact"
    if BUSINESS_LINE_RE.search(lowered):
        return "noise"
    if NOISE_LINE_RE.search(lowered):
        return "noise"
    
    # Check if it's a location (capitalized, no digits, short)
    if not any(ch.isdigit() for ch in line) and (RENT_WORD_RE.search(lowered) or SALE_WORD_RE.search(lowered) or PROPERTY_SECTION_RE.search(lowered)):
        if _listing_signal_score(line) <= 2:
            return "header"
    if _listing_signal_score(line) >= 4:
        return "listing"
    if DETAIL_WORD_RE.search(lowered) or LOCATION_STOP_PHRASES_RE.search(lowered):
        return "detail"
    if (not any(ch.isdigit() for ch in line) and 
        len(line.split()) <= 3 and 
        line[0].isupper() and
        not RENT_WORD_RE.search(lowered) and
        not SALE_WORD_RE.search(lowered)):
        # Filter out common header words
        if lowered in {'avl', 'rentals', 'avl rentals', 'properties', 'available', 'sunshine properties'}:
            return "header"
        return "location"
    if _looks_like_location(line):
        if len(line.split()) <= 3:
            return "location"
        return "entity"
    if RATE_RE.search(line):
        return "detail"
    return "other"


def _clean_listing_line(line: str) -> str:
    line = line.strip()
    line = EMOJI_RE.sub(" ", line)
    line = line.replace("*", " ")
    line = BULLET_RE.sub("", line)
    line = re.sub(r"\s+", " ", line).strip(" *_-.,:|")
    return line


def _looks_like_listing_line(line: str) -> bool:
    cleaned_line = _clean_listing_line(line)
    return _classify_line_role(cleaned_line) == "listing"


def _compose_listing_message(buffer: ListingBuffer, contact_lines: list[str]) -> ParsedMessage | None:
    """Compose a listing message with proper structure."""
    if not buffer.lines:
        return None
    
    # Build message with location context embedded
    parts = []
    
    # Add location at the start for better extraction
    if buffer.context.location:
        parts.append(f"Location {buffer.context.location}")
        if buffer.context.project:
            parts.append(f"Project {buffer.context.project}")
    
    # Add listing details
    parts.extend(buffer.lines)
    
    # Add contact info
    if contact_lines:
        parts.extend(contact_lines)
    
    message = "\n".join(part for part in parts if part)
    
    # Verify this is a valid listing
    if not any(_listing_signal_score(line) >= 3 for line in buffer.lines):
        return None
    
    return ParsedMessage(timestamp=datetime.min, sender="", message=message, source="")


def _listing_has_price(lines: list[str]) -> bool:
    return any(PRICE_TOKEN_RE.search(line) or RATE_RE.search(line) for line in lines)


def _split_bulk_message(msg: ParsedMessage) -> list[ParsedMessage]:
    """Split bulk broker messages into individual listings."""
    raw_text = msg.raw_message or msg.message
    
    # Pre-process: If text has no line breaks but has asterisks, split on asterisks
    if '\n' not in raw_text and '*' in raw_text:
        starred_segments = re.findall(r'\*([^*]+)\*([^*]*)', raw_text)
        if len(starred_segments) >= 2:
            reconstructed = []
            for label, details in starred_segments:
                combined = f"{label.strip()} {details.strip()}".strip()
                if combined:
                    reconstructed.append(combined)
            raw_text = '\n'.join(reconstructed)
        else:
            # Fallback for malformed star blocks.
            parts = re.split(r'(\d+(?:\.\d+)?\s*bhk)', raw_text, flags=re.IGNORECASE)
            reconstructed = []
            i = 0
            while i < len(parts):
                part = parts[i].strip()
                if re.match(r'\d+(?:\.\d+)?\s*bhk$', part, re.IGNORECASE):
                    if i + 1 < len(parts):
                        reconstructed.append(part + ' ' + parts[i + 1].strip())
                        i += 2
                    else:
                        reconstructed.append(part)
                        i += 1
                elif part:
                    reconstructed.append(part)
                    i += 1
                else:
                    i += 1
            raw_text = '\n'.join(reconstructed)
    
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    
    # Clean lines but preserve structure
    cleaned_lines = []
    for line in lines:
        cleaned = EMOJI_RE.sub(" ", line)
        cleaned = cleaned.replace("*", "").strip()
        if cleaned and not re.match(r'^[🔖🔑\s]+$', line):
            cleaned_lines.append(cleaned)
    
    # Count listing indicators
    listing_like_count = sum(1 for line in cleaned_lines if _classify_line_role(line) == "listing")
    
    # If less than 2 listings, treat as single message
    if listing_like_count < 2:
        return [ParsedMessage(
            timestamp=msg.timestamp,
            sender=msg.sender,
            message=msg.message,
            source=msg.source,
            raw_message=raw_text
        )]

    # Extract contact info once
    contact_lines = [line for line in cleaned_lines if _classify_line_role(line) == "contact"]
    
    # Split into individual listings
    context = ListingContext()
    current: ListingBuffer | None = None
    exploded: list[ParsedMessage] = []
    
    for line in cleaned_lines:
        role = _classify_line_role(line)
        
        # Skip noise
        if role in {"empty", "contact", "noise"}:
            continue
        
        # Header (section title like "Available For Rent")
        if role == "header":
            if current and _listing_has_price(current.lines):
                built = _compose_listing_message(current, contact_lines)
                if built:
                    built.timestamp = msg.timestamp
                    built.sender = msg.sender
                    built.source = msg.source
                    built.raw_message = raw_text
                    exploded.append(built)
                current = None
            context.section_header = line
            inferred_txn = extract_transaction_type(normalize_text(line))
            if inferred_txn:
                context.transaction = inferred_txn
            continue
        
        # Location (area name)
        if role == "location":
            if current and not _listing_has_price(current.lines):
                if not current.context.location or re.match(r'^\d+(?:\.\d+)?\s*[- ]?bhk\b', current.context.location, re.IGNORECASE):
                    current.context.location = line
                    continue
                if current.context.location and not current.context.project:
                    current.context.project = line
                    continue
            if current is None and context.location and not context.project:
                context.project = line
                continue

            # Flush current listing if it has price
            if current and _listing_has_price(current.lines):
                built = _compose_listing_message(current, contact_lines)
                if built:
                    built.timestamp = msg.timestamp
                    built.sender = msg.sender
                    built.source = msg.source
                    built.raw_message = raw_text
                    exploded.append(built)
                current = None
            
            # Update context
            context.location = line
            context.project = ""
            continue
        
        # Entity (project/building name)
        if role == "entity":
            if context.location and not context.project:
                context.project = line
            elif not context.location:
                context.location = line
            continue
        
        # Listing line (has BHK, price, etc)
        if role in {"listing", "detail", "other"}:
            line_context = ListingContext(
                section_header=context.section_header,
                transaction=context.transaction,
                location=context.location,
                project=context.project,
                property_context=context.property_context,
            )
            
            if current is None:
                current = ListingBuffer(context=line_context, lines=[line])
                continue
            
            # Check if this starts a new listing
            starts_new_listing = (
                role == "listing" and 
                _listing_has_price(current.lines) and
                (BHK_LINE_RE.search(line) or STUDIO_RE.search(line) or SIZE_RE.search(line))
            )
            
            if starts_new_listing:
                built = _compose_listing_message(current, contact_lines)
                if built:
                    built.timestamp = msg.timestamp
                    built.sender = msg.sender
                    built.source = msg.source
                    built.raw_message = raw_text
                    exploded.append(built)
                current = ListingBuffer(context=line_context, lines=[line])
            else:
                current.lines.append(line)

    # Don't forget the last listing
    if current:
        built = _compose_listing_message(current, contact_lines)
        if built:
            built.timestamp = msg.timestamp
            built.sender = msg.sender
            built.source = msg.source
            built.raw_message = raw_text
            exploded.append(built)

    if not exploded:
        return [ParsedMessage(
            timestamp=msg.timestamp,
            sender=msg.sender,
            message=msg.message,
            source=msg.source,
            raw_message=raw_text
        )]
    
    return exploded


def extract_property(cleaned: str, property_mapper: MappingResolver) -> tuple[str, bool]:
    return property_mapper.resolve(cleaned)


def extract_location(cleaned: str, location_mapper: MappingResolver) -> tuple[str, bool]:
    """Extract location - use geocoding only as last resort when location is blank."""
    # 1. Try mapping first (highest priority)
    location, ambiguous = location_mapper.resolve(cleaned)
    if location or ambiguous:
        return location, ambiguous
    
    # 2. Try keyword inference (fast, local)
    inferred = infer_location_from_keywords(cleaned)
    if inferred:
        return inferred, False
    
    # 3. Try anchored context extraction instead of guessing arbitrary phrases.
    contextual, contextual_ambiguous = extract_location_from_context(cleaned, location_mapper)
    if contextual or contextual_ambiguous:
        return contextual, contextual_ambiguous
    
    # 4. LAST RESORT: Use geocoding only if location is still blank
    if USE_GEOCODING:
        try:
            from .location_service import extract_location_enhanced
            external_location = extract_location_enhanced(cleaned, city="Pune")
            if external_location:
                return external_location, False
        except Exception:
            pass
    
    return "", False


def build_summary(
    lead_type: str,
    transaction: str,
    location: str,
    property_type: str,
    bhk: int | None,
    bmin: int | None,
    bmax: int | None,
    phone: str,
    flags: list[str],
    area_sqft: int | None = None,
    furnishing: str = "",
    project: str = "",
) -> str:
    budget = "Unknown"
    if bmin is not None and bmax is not None:
        budget = f"{bmin}-{bmax}" if bmin != bmax else str(bmin)
    
    parts = [
        f"Type={lead_type or 'Unknown'}",
        f"Txn={transaction or 'Unknown'}",
        f"Location={location or 'Unknown'}",
        f"Property={property_type or 'Unknown'}",
        f"BHK={bhk if bhk is not None else 'Unknown'}",
        f"Budget={budget}",
    ]
    
    if area_sqft:
        parts.append(f"Area={area_sqft}sqft")
    if furnishing:
        parts.append(f"Furnishing={furnishing}")
    if project:
        parts.append(f"Project={project}")
    
    parts.extend([
        f"Phone={phone or 'Unknown'}",
        f"Flags={','.join(sorted(set(flags))) if flags else 'none'}",
    ])
    
    return " | ".join(parts)


def confidence_scores(
    location: str,
    location_ambiguous: bool,
    bmin: int | None,
    bmax: int | None,
    bhk: int | None,
    bhk_ambiguous: bool,
) -> tuple[float, float, float, list[str]]:
    flags: list[str] = []

    if location and not location_ambiguous:
        location_conf = 1.0
    elif location_ambiguous:
        location_conf = 0.25
        flags.append("location_ambiguous")
    else:
        location_conf = 0.0
        flags.append("location_missing")

    if bmin is not None and bmax is not None:
        budget_conf = 1.0
        if bmin == bmax:
            flags.append("budget_inferred")
    else:
        budget_conf = 0.0
        flags.append("budget_missing")

    if bhk is not None:
        bhk_conf = 1.0
    elif bhk_ambiguous:
        bhk_conf = 0.25
        flags.append("bhk_ambiguous")
    else:
        bhk_conf = 0.0
        flags.append("bhk_missing")

    return location_conf, budget_conf, bhk_conf, flags


def build_contact_id(phone: str, name: str) -> str:
    seed = phone or normalize_text(name) or "unknown-contact"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def build_lead_id(contact_id: str, lead_signature: str, first_seen_date: str) -> str:
    seed = f"{contact_id}|{lead_signature}|{first_seen_date}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:14]


def to_structured(
    messages: Iterable[ParsedMessage],
    location_mapper: MappingResolver,
    property_mapper: MappingResolver,
    score_weights: dict[str, float],
) -> list[StructuredLead]:
    leads: list[StructuredLead] = []

    expanded_messages: list[ParsedMessage] = []
    for msg in messages:
        expanded_messages.extend(_split_bulk_message(msg))

    for msg in expanded_messages:
        cleaned = normalize_text(msg.message)
        lead_type = classify_message(cleaned)
        extracted_name = extract_name_from_text(msg.message) or extract_name(msg.sender)
        raw_message = msg.raw_message or msg.message
        if lead_type == "Ignore":
            leads.append(
                StructuredLead(
                    {
                        "Date": msg.timestamp.date().isoformat(),
                        "Month": month_bucket(msg.timestamp),
                        "Week": week_bucket(msg.timestamp),
                        "Time": msg.timestamp.time().strftime("%H:%M:%S"),
                        "Source": msg.source,
                        "Type": "Ignore",
                        "Name": extracted_name,
                        "Raw Message": raw_message,
                        "Cleaned Message": cleaned,
                        "Extraction Status": "Failed",
                        "Extraction Flags": "ignored_message",
                        "Repeat Count": 1,
                        "data_status": "RAW",
                        "First Seen": msg.timestamp.isoformat(sep=" "),
                        "Last Seen": msg.timestamp.isoformat(sep=" "),
                        "Incomplete Data": "Yes",
                    }
                )
            )
            continue

        bmin, bmax, inferred_budget = extract_budget(cleaned)
        bhk, bhk_ambiguous = extract_bhk(cleaned)
        transaction = extract_transaction_type(cleaned, bmin, bmax)  # Pass budget for inference
        phone = extract_phone(msg.message, msg.sender)
        
        # Extract location from message
        location_from_original = ""
        
        # Check if message starts with "Location " (from bulk splitting)
        if msg.message.startswith("Location "):
            # Extract location from composed message
            parts = msg.message.split()
            if len(parts) >= 2:
                # Find where location name ends (before BHK/price)
                location_words = []
                for i, word in enumerate(parts[1:], 1):  # Skip "Location"
                    if re.search(r'\d+\s*(bhk|k|l|cr|sqft)', word, re.IGNORECASE):
                        break
                    if re.search(r'\b(project|flat|rent|sale|furnished|garden|floor)\b', word, re.IGNORECASE):
                        break
                    location_words.append(word)
                if location_words:
                    location_from_original = ' '.join(location_words)
        else:
            # Extract project names from asterisks (e.g., *Kumar Presidency*)
            asterisk_parts = msg.message.split('*')
            for part in asterisk_parts:
                part = part.strip()
                if not part or len(part) > 50:
                    continue
                
                # Skip common header phrases and property details
                part_lower = part.lower()
                if any(phrase in part_lower for phrase in [
                    'rental properties', 'avl rentals', 'rentals', 'available on rent', 'available for rent',
                    'for sale', 'available for sale', 'properties', 'on rent', 'for rent', 'sunshine properties',
                    'avl', 'available', 'all allowed', 'immediate possession', 'family', 'bachelor',
                    'shops for rent', 'offices for rent', 'office for rent', 'rental flats'
                ]):
                    continue
                
                # Check if it's a project/location name (capitalized, no BHK/price, 1-5 words)
                words = part.split()
                if (len(words) >= 1 and len(words) <= 5 and
                    part[0].isupper() and
                    not re.search(r'\d+\s*(bhk|k|l|cr|sqft|sq)', part, re.IGNORECASE) and
                    not re.search(r'\b(flatrent|flat|rent|sale|furnished|family|bachelor|allowed|only|garden|floor|ground|irfan|on|avl|available|bunglow|possession|immediate|negotiable|call)\b', part, re.IGNORECASE)):
                    location_from_original = part
                    break
        
        location, location_ambiguous = extract_location(cleaned, location_mapper)
        
        # Use location from original if mapping failed
        if not location and not location_ambiguous and location_from_original:
            location = location_from_original
        
        # If still no location, try area abbreviation expansion
        if not location and not location_ambiguous:
            location = expand_area_abbreviation(msg.message)
        
        # If still no location, try keyword inference
        if not location and not location_ambiguous:
            location = infer_location_from_keywords(cleaned)
        
        # If still no location, try extracting project name from text patterns
        if not location and not location_ambiguous:
            # Pattern 1: "at [project name]"
            at_match = re.search(
                r'\bat\s+([a-z][a-z\s]{2,40}?)(?=\s+(?:rent|sale|phase|furnished|unfurnished|semi|fully|\d)|$)',
                cleaned,
                re.IGNORECASE,
            )
            if at_match:
                candidate = _clean_location_candidate(at_match.group(1))
                if _is_valid_location_candidate(candidate):
                    location = candidate.title()
            else:
                # Pattern 2: "bhk [project name] rent"
                bhk_loc_match = re.search(
                    r'\d+(?:\.\d+)?\s*bhk\s+([a-z][a-z\s]{2,40}?)(?=\s+(?:rent|sale|price|furnished|unfurnished|semi|fully)|$)',
                    cleaned,
                    re.IGNORECASE,
                )
                if bhk_loc_match:
                    candidate = _clean_location_candidate(bhk_loc_match.group(1))
                    if _is_valid_location_candidate(candidate):
                        location = candidate.title()
        
        # LAST RESORT: Use geocoding only if location is still blank
        # This is slow, so only use when absolutely necessary
        if not location and not location_ambiguous and USE_GEOCODING:
            try:
                from .location_service import extract_location_enhanced
                external_location = extract_location_enhanced(cleaned, city="Pune")
                if external_location:
                    location = external_location
            except Exception:
                pass  # Gracefully degrade if service not available
            
        property_type, property_ambiguous = extract_property(cleaned, property_mapper)
        if property_ambiguous:
            property_type = ""
        property_hint = extract_property_hint(cleaned)
        
        # Extract additional fields
        area_sqft = extract_area_sqft(cleaned)
        furnishing = extract_furnishing(cleaned)
        project_name = extract_project_name(msg.message)
        cleaned_project_name = re.sub(r'^\d+(?:\.\d+)?\s*[- ]?bhk\s+', '', project_name, flags=re.IGNORECASE).strip()
        if cleaned_project_name and (not location or re.match(r'^\d+(?:\.\d+)?\s*[- ]?bhk\b', location, re.IGNORECASE)):
            location = cleaned_project_name
        
        # Infer property type if missing AND no mapping was found
        if not property_type and not property_ambiguous:
            # Check if mapping table has entries (more than just header)
            has_mapping_entries = len(property_mapper.entries) > 0
            property_type = infer_property_type(bhk, bmin, transaction, property_hint, has_mapping_entries)

        location_conf, budget_conf, bhk_conf, flags = confidence_scores(
            location, location_ambiguous, bmin, bmax, bhk, bhk_ambiguous
        )
        if inferred_budget and bmin is not None:
            flags.append("budget_inferred")
        if property_ambiguous:
            flags.append("property_ambiguous")
        elif not property_type:
            flags.append("property_missing")
        else:
            # Check if property was inferred vs mapped
            mapped_property, _ = extract_property(cleaned, property_mapper)
            if not mapped_property and property_type:
                flags.append("property_inferred")
        if not transaction:
            flags.append("transaction_missing")
        else:
            # Check if transaction was inferred
            explicit_txn = RENT_WORD_RE.search(cleaned) or SALE_WORD_RE.search(cleaned)
            if not explicit_txn:
                flags.append("transaction_inferred")
        if not phone:
            flags.append("phone_missing")
        if not extracted_name:
            flags.append("name_missing")
        if phone and len(phone) == 10:
            flags.append("phone_present")
        if area_sqft:
            flags.append("area_present")
        if furnishing:
            flags.append("furnishing_present")
        if project_name:
            flags.append("project_present")

        confidence_score = round(
            (
                location_conf * score_weights.get("confidence_location", 1)
                + budget_conf * score_weights.get("confidence_budget", 1)
                + bhk_conf * score_weights.get("confidence_bhk", 1)
            )
            / max(
                score_weights.get("confidence_location", 1)
                + score_weights.get("confidence_budget", 1)
                + score_weights.get("confidence_bhk", 1),
                1,
            )
            * 100,
            2,
        )

        status = "Success"
        if not location or not property_type or bhk is None or bmin is None or bmax is None or not transaction:
            status = "Partial"

        incomplete = "Yes" if status != "Success" else "No"
        contact_id = build_contact_id(phone, extracted_name or msg.sender)
        lead_signature = f"{lead_type}|{transaction}|{location}|{property_type}|{bhk}|{bmin}|{bmax}"
        lead_id = build_lead_id(contact_id, lead_signature, msg.timestamp.date().isoformat())

        summary = build_summary(lead_type, transaction, location, property_type, bhk, bmin, bmax, phone, flags, area_sqft, furnishing, project_name)

        leads.append(
            StructuredLead(
                {
                    "Date": msg.timestamp.date().isoformat(),
                    "Month": month_bucket(msg.timestamp),
                    "Week": week_bucket(msg.timestamp),
                    "Time": msg.timestamp.time().strftime("%H:%M:%S"),
                    "Source": msg.source,
                    "Type": lead_type,
                    "Transaction Type": transaction,
                    "Location": location,
                    "Property Type": property_type,
                    "BHK": bhk,
                    "Budget Range": budget_bucket(bmin, bmax),
                    "Budget_Min": bmin,
                    "Budget_Max": bmax,
                    "Area_Sqft": area_sqft,
                    "Furnishing": furnishing,
                    "Project_Name": project_name,
                    "Contact Number": phone,
                    "Name": extracted_name,
                    "Raw Message": raw_message,
                    "Cleaned Message": cleaned,
                    "Lead Summary": summary,
                    "Extraction Status": status,
                    "Confidence Score": confidence_score,
                    "location_confidence": location_conf,
                    "budget_confidence": budget_conf,
                    "bhk_confidence": bhk_conf,
                    "Extraction Flags": ",".join(sorted(set(flags))),
                    "Lead_ID": lead_id,
                    "Contact_ID": contact_id,
                    "Repeat Count": 1,
                    "Incomplete Data": incomplete,
                    "data_status": "RAW",
                    "First Seen": msg.timestamp.isoformat(sep=" "),
                    "Last Seen": msg.timestamp.isoformat(sep=" "),
                }
            )
        )
    return leads


def urgency_hit(cleaned_message: str) -> int:
    return 1 if any(word in cleaned_message for word in URGENCY_WORDS) else 0


def parse_iso(value: str) -> datetime:
    return parse_datetime(value)
