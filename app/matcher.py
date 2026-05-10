from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from .analytics import month_bucket, week_bucket
from .dedup import completeness_score, recency_score
from .extractor import urgency_hit
from .schemas import StructuredLead


def _to_float(v: object, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _budget_overlap(a_min: float, a_max: float, b_min: float, b_max: float) -> float:
    if min(a_max, b_max) < max(a_min, b_min):
        return 0.0
    intersection = min(a_max, b_max) - max(a_min, b_min)
    union = max(a_max, b_max) - min(a_min, b_min)
    if union <= 0:
        return 1.0
    return max(0.0, min(1.0, intersection / union))


def _transaction_match(buyer_txn: str, seller_txn: str) -> float:
    if not buyer_txn or not seller_txn:
        return 0.0
    return 1.0 if buyer_txn == seller_txn else 0.0


def _format_budget_range(min_value: object, max_value: object) -> str:
    low = _to_float(min_value, default=-1)
    high = _to_float(max_value, default=-1)
    if low < 0 or high < 0:
        return "unknown"
    if low == high:
        return str(int(low))
    return f"{int(low)}-{int(high)}"


def _eligible_leads(leads: list[StructuredLead]) -> list[StructuredLead]:
    """Filter leads that are eligible for matching - must have phone and core fields."""
    return [
        lead
        for lead in leads
        if lead.values.get("Type") in {"Buyer", "Seller"}
        and lead.values.get("Contact Number")  # Must have phone
        and lead.values.get("Location")  # Must have location
        and lead.values.get("Transaction Type")  # Must have transaction type
    ]


def compute_matches(leads: list[StructuredLead], weights: dict[str, float], threshold: float, now: datetime) -> list[dict[str, object]]:
    eligible = _eligible_leads(leads)
    buyers = [l for l in eligible if l.values.get("Type") == "Buyer"]
    sellers = [l for l in eligible if l.values.get("Type") == "Seller"]
    rows: list[dict[str, object]] = []

    w_loc = weights.get("match_location", 1)
    w_prop = weights.get("match_property", 1)
    w_bhk = weights.get("match_bhk", 1)
    w_budget = weights.get("match_budget", 1)
    w_txn = weights.get("match_transaction", 1)
    w_recency = weights.get("match_recency", 1)
    w_complete = weights.get("match_completeness", 1)
    w_conf = weights.get("match_confidence", 1)
    total_w = max(w_loc + w_prop + w_bhk + w_budget + w_txn + w_recency + w_complete + w_conf, 1)

    for buyer in buyers:
        for seller in sellers:
            location_score = 1.0 if buyer.values.get("Location") and buyer.values.get("Location") == seller.values.get("Location") else 0.0
            property_score = 1.0 if buyer.values.get("Property Type") and buyer.values.get("Property Type") == seller.values.get("Property Type") else 0.0
            bhk_score = 1.0 if buyer.values.get("BHK") and buyer.values.get("BHK") == seller.values.get("BHK") else 0.0

            bmin = _to_float(buyer.values.get("Budget_Min"))
            bmax = _to_float(buyer.values.get("Budget_Max"))
            smin = _to_float(seller.values.get("Budget_Min"))
            smax = _to_float(seller.values.get("Budget_Max"))
            budget_score = _budget_overlap(bmin, bmax, smin, smax) if all([bmin, bmax, smin, smax]) else 0.0

            txn_score = _transaction_match(str(buyer.values.get("Transaction Type", "")), str(seller.values.get("Transaction Type", "")))
            pair_recency = (recency_score(str(buyer.values.get("Last Seen")), now) + recency_score(str(seller.values.get("Last Seen")), now)) / 2
            pair_complete = (completeness_score(buyer) + completeness_score(seller)) / 2
            pair_conf = (_to_float(buyer.values.get("Confidence Score")) + _to_float(seller.values.get("Confidence Score"))) / 200

            final_score = (
                location_score * w_loc
                + property_score * w_prop
                + bhk_score * w_bhk
                + budget_score * w_budget
                + txn_score * w_txn
                + pair_recency * w_recency
                + pair_complete * w_complete
                + pair_conf * w_conf
            ) / total_w * 100

            if final_score < threshold:
                continue

            # Build readable match reason
            reason_parts = []
            if location_score == 1.0:
                reason_parts.append(f"Location: {buyer.values.get('Location')}")
            if property_score == 1.0 and buyer.values.get('Property Type'):
                reason_parts.append(f"Property: {buyer.values.get('Property Type')}")
            if bhk_score == 1.0:
                reason_parts.append(f"BHK: {buyer.values.get('BHK')}")
            if budget_score > 0.5:
                reason_parts.append(f"Budget overlap: {round(budget_score*100)}%")
            if txn_score == 1.0:
                reason_parts.append(f"Transaction: {buyer.values.get('Transaction Type')}")
            if pair_recency > 0.7:
                reason_parts.append("Recent leads")
            
            match_reason = " | ".join(reason_parts) if reason_parts else "Partial match"

            rows.append(
                {
                    "Date": now.date().isoformat(),
                    "Month": month_bucket(now),
                    "Week": week_bucket(now),
                    "Buyer Lead_ID": buyer.values.get("Lead_ID"),
                    "Buyer Name": buyer.values.get("Name"),
                    "Buyer Phone": buyer.values.get("Contact Number"),
                    "Seller Lead_ID": seller.values.get("Lead_ID"),
                    "Seller Name": seller.values.get("Name"),
                    "Seller Phone": seller.values.get("Contact Number"),
                    "Location": buyer.values.get("Location") or seller.values.get("Location"),
                    "Property Type": buyer.values.get("Property Type") or seller.values.get("Property Type"),
                    "BHK": buyer.values.get("BHK") or seller.values.get("BHK"),
                    "Buyer Budget": _format_budget_range(buyer.values.get('Budget_Min'), buyer.values.get('Budget_Max')),
                    "Seller Budget": _format_budget_range(seller.values.get('Budget_Min'), seller.values.get('Budget_Max')),
                    "Match Score": round(final_score, 2),
                    "Match Reason": match_reason,
                    "Matched At": now.isoformat(sep=" "),
                }
            )

    rows.sort(key=lambda r: float(r["Match Score"]), reverse=True)
    return rows


def compute_priority(leads: list[StructuredLead], matches: list[dict[str, object]], weights: dict[str, float], now: datetime) -> None:
    max_match_for_lead: dict[str, float] = defaultdict(float)
    for m in matches:
        score = _to_float(m.get("Match Score"))
        max_match_for_lead[str(m.get("Buyer Lead_ID"))] = max(max_match_for_lead[str(m.get("Buyer Lead_ID"))], score)
        max_match_for_lead[str(m.get("Seller Lead_ID"))] = max(max_match_for_lead[str(m.get("Seller Lead_ID"))], score)

    w_recency = weights.get("priority_recency", 1)
    w_complete = weights.get("priority_completeness", 1)
    w_urgency = weights.get("priority_urgency", 1)
    w_budget = weights.get("priority_budget", 1)
    w_match = weights.get("priority_match_strength", 1)
    w_conf = weights.get("priority_confidence", 1)
    total = max(w_recency + w_complete + w_urgency + w_budget + w_match + w_conf, 1)

    for lead in leads:
        recency = recency_score(str(lead.values.get("Last Seen")), now)
        complete = completeness_score(lead)
        urgency = urgency_hit(str(lead.values.get("Cleaned Message", "")))
        budget_presence = 1.0 if lead.values.get("Budget_Min") and lead.values.get("Budget_Max") else 0.0
        match_strength = max_match_for_lead.get(str(lead.values.get("Lead_ID")), 0.0) / 100
        conf = _to_float(lead.values.get("Confidence Score"), 0.0) / 100

        score = (
            recency * w_recency
            + complete * w_complete
            + urgency * w_urgency
            + budget_presence * w_budget
            + match_strength * w_match
            + conf * w_conf
        ) / total * 100

        lead.values["Priority Score"] = round(score, 2)
        
        # Build readable priority reason
        reasons = []
        if recency > 0.8:
            reasons.append("Very recent")
        elif recency > 0.5:
            reasons.append("Recent")
        if complete >= 0.9:
            reasons.append("Complete data")
        if urgency:
            reasons.append("Urgent")
        if budget_presence:
            reasons.append("Has budget")
        if match_strength > 0.7:
            reasons.append("Strong match available")
        elif match_strength > 0.4:
            reasons.append("Match available")
        if conf > 0.8:
            reasons.append("High confidence")
        
        lead.values["Priority Reason"] = " | ".join(reasons) if reasons else "Standard lead"


def top_leads(leads: list[StructuredLead], top_n: int = 10) -> list[StructuredLead]:
    """Get top leads - filter out incomplete leads without phone."""
    candidates = [
        lead for lead in leads
        if lead.values.get("Type") in {"Buyer", "Seller"}
        and lead.values.get("Contact Number")  # Must have phone to be actionable
        and lead.values.get("Location")  # Must have location
    ]
    candidates.sort(key=lambda l: float(l.values.get("Priority Score", 0) or 0), reverse=True)
    return candidates[:top_n]


def demand_summary(leads: list[StructuredLead]) -> list[dict[str, object]]:
    counts: dict[tuple[str, str, object, object], int] = defaultdict(int)
    for lead in _eligible_leads(leads):
        if lead.values.get("Type") != "Buyer":
            continue
        # Convert None to empty string for sorting
        budget_min = lead.values.get("Budget_Min")
        budget_max = lead.values.get("Budget_Max")
        key = (
            str(lead.values.get("Location", "")),
            str(lead.values.get("BHK", "")),
            budget_min if budget_min is not None else 0,
            budget_max if budget_max is not None else 0,
        )
        counts[key] += 1
    return [
        {"Location": k[0], "BHK": k[1], "Budget_Min": k[2], "Budget_Max": k[3], "Count": v}
        for k, v in sorted(counts.items())
    ]


def supply_summary(leads: list[StructuredLead]) -> list[dict[str, object]]:
    counts: dict[tuple[str, str, str, object, object], int] = defaultdict(int)
    for lead in _eligible_leads(leads):
        if lead.values.get("Type") != "Seller":
            continue
        # Convert None to 0 for sorting
        budget_min = lead.values.get("Budget_Min")
        budget_max = lead.values.get("Budget_Max")
        key = (
            str(lead.values.get("Location", "")),
            str(lead.values.get("Property Type", "")),
            str(lead.values.get("BHK", "")),
            budget_min if budget_min is not None else 0,
            budget_max if budget_max is not None else 0,
        )
        counts[key] += 1
    return [
        {"Location": k[0], "Property Type": k[1], "BHK": k[2], "Price_Min": k[3], "Price_Max": k[4], "Count": v}
        for k, v in sorted(counts.items())
    ]
