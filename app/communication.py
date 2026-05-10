from __future__ import annotations

from typing import Any

WHATSAPP_TEMPLATES = {
    "buyer_match_notification": """Hello {buyer_name},

We have found a property that matches your requirement:

📍 Location: {location}
🏠 Property Type: {property_type}
🛏️ BHK: {bhk}
💰 Budget: {budget}

Broker Contact:
👤 {broker_name}
📞 {broker_phone}

{match_reason}

Would you like to schedule a visit?

Best regards,
Shelters Realty""",
    
    "seller_match_notification": """Hello {seller_name},

We have found a potential buyer for your property:

📍 Location: {location}
🏠 Property Type: {property_type}
🛏️ BHK: {bhk}
💰 Budget: {budget}

Buyer Contact:
👤 {buyer_name}
📞 {buyer_phone}

{match_reason}

Please let us know if you'd like to connect.

Best regards,
Shelters Realty""",
    
    "follow_up_reminder": """Hello {name},

This is a follow-up regarding your property requirement in {location}.

Current Status: {status}

{notes}

Please feel free to reach out if you have any questions.

Best regards,
Shelters Realty
📞 Contact: contact@sheltersrealty.co.in""",
    
    "welcome_message": """Hello {name},

Thank you for your interest in properties in {location}.

We have received your requirement:
🏠 {property_type}
🛏️ {bhk} BHK
💰 Budget: {budget}

Our team will get back to you shortly with matching properties.

Best regards,
Shelters Realty
📞 Contact: contact@sheltersrealty.co.in""",
    
    "property_inquiry": """Hello,

I am interested in the property:
📍 {location}
🏠 {property_type}
🛏️ {bhk} BHK
💰 Budget: {budget}

Please share more details.

Thank you.""",
}


def _safe_str(value: object) -> str:
    """Safely convert value to string."""
    return "" if value is None else str(value).strip()


def generate_whatsapp_message(template_name: str, data: dict[str, Any]) -> str:
    """
    Generate WhatsApp-ready message from template.
    
    Args:
        template_name: Name of the template to use
        data: Dictionary with placeholder values
        
    Returns:
        Formatted message ready to send via WhatsApp
        
    Raises:
        ValueError: If template not found or required placeholders missing
    """
    if template_name not in WHATSAPP_TEMPLATES:
        raise ValueError(f"Unknown template: {template_name}")
    
    template = WHATSAPP_TEMPLATES[template_name]
    
    # Normalize all data values
    normalized_data = {key: _safe_str(value) for key, value in data.items()}
    
    # Check for missing required placeholders
    import re
    placeholders = set(re.findall(r'\{(\w+)\}', template))
    missing = placeholders - set(normalized_data.keys())
    
    if missing:
        # Fill missing placeholders with empty string or default
        for key in missing:
            normalized_data[key] = ""
    
    try:
        message = template.format(**normalized_data)
    except KeyError as exc:
        raise ValueError(f"Missing required placeholder: {exc}") from exc
    
    # Remove empty lines and extra whitespace
    lines = [line.strip() for line in message.split('\n')]
    cleaned_lines = [line for line in lines if line]
    
    return '\n'.join(cleaned_lines)


def generate_buyer_match_message(buyer_data: dict[str, Any], match_data: dict[str, Any]) -> str:
    """Generate WhatsApp message for buyer match notification."""
    data = {
        "buyer_name": buyer_data.get("name", ""),
        "location": match_data.get("location", ""),
        "property_type": match_data.get("property_type", ""),
        "bhk": match_data.get("bhk", ""),
        "budget": match_data.get("budget", ""),
        "broker_name": match_data.get("broker_name", ""),
        "broker_phone": match_data.get("broker_phone", ""),
        "match_reason": match_data.get("match_reason", ""),
    }
    return generate_whatsapp_message("buyer_match_notification", data)


def generate_seller_match_message(seller_data: dict[str, Any], match_data: dict[str, Any]) -> str:
    """Generate WhatsApp message for seller match notification."""
    data = {
        "seller_name": seller_data.get("name", ""),
        "location": match_data.get("location", ""),
        "property_type": match_data.get("property_type", ""),
        "bhk": match_data.get("bhk", ""),
        "budget": match_data.get("budget", ""),
        "buyer_name": match_data.get("buyer_name", ""),
        "buyer_phone": match_data.get("buyer_phone", ""),
        "match_reason": match_data.get("match_reason", ""),
    }
    return generate_whatsapp_message("seller_match_notification", data)


def generate_follow_up_message(lead_data: dict[str, Any], execution_data: dict[str, Any]) -> str:
    """Generate WhatsApp message for follow-up reminder."""
    data = {
        "name": lead_data.get("name", ""),
        "location": lead_data.get("location", ""),
        "status": execution_data.get("status", ""),
        "notes": execution_data.get("notes", ""),
    }
    return generate_whatsapp_message("follow_up_reminder", data)


def generate_welcome_message(lead_data: dict[str, Any]) -> str:
    """Generate WhatsApp welcome message for new lead."""
    data = {
        "name": lead_data.get("name", ""),
        "location": lead_data.get("location", ""),
        "property_type": lead_data.get("property_type", ""),
        "bhk": lead_data.get("bhk", ""),
        "budget": lead_data.get("budget", ""),
    }
    return generate_whatsapp_message("welcome_message", data)


def generate_property_inquiry_message(property_data: dict[str, Any]) -> str:
    """Generate WhatsApp message for property inquiry."""
    data = {
        "location": property_data.get("location", ""),
        "property_type": property_data.get("property_type", ""),
        "bhk": property_data.get("bhk", ""),
        "budget": property_data.get("budget", ""),
    }
    return generate_whatsapp_message("property_inquiry", data)


def get_whatsapp_url(phone: str, message: str) -> str:
    """
    Generate WhatsApp URL with pre-filled message.
    
    Args:
        phone: Phone number (with country code)
        message: Pre-filled message text
        
    Returns:
        WhatsApp URL ready to open
    """
    import urllib.parse
    
    # Clean phone number (remove spaces, dashes, etc.)
    clean_phone = "".join(c for c in phone if c.isdigit())
    
    # URL encode the message
    encoded_message = urllib.parse.quote(message)
    
    return f"https://wa.me/{clean_phone}?text={encoded_message}"


def get_available_templates() -> list[str]:
    """Get list of available template names."""
    return list(WHATSAPP_TEMPLATES.keys())
