from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import heapq
import math

from .analytics import month_bucket, week_bucket
from .dedup import completeness_score, recency_score
from .extractor import urgency_hit
from .schemas import StructuredLead

_MAX_MATCHES_PER_BUYER = 25
_BUDGET_BAND_SIZE = 1_000_000.0
_PROPERTY_FAMILIES = {
    "apartment": {"flat", "apartment", "residential apartment"},
    "villa": {"villa", "bungalow", "independent house", "row house", "duplex"},
    "land": {"plot", "land"},
    "commercial": {"office", "commercial", "shop", "showroom", "warehouse", "industrial"},
}


@dataclass(frozen=True)
class _PreparedLead:
    lead_id: str
    name: str
    phone: str
    lead_type: str
    location: str
    property_type: str
    bhk: object
    budget_min: float
    budget_max: float
    transaction: str
    recency: float
    completeness: float
    confidence: float
    budget_text: str


@dataclass
class _SellerBucket:
    sellers: list[_PreparedLead] = field(default_factory=list)
    by_budget_band: dict[int, list[_PreparedLead]] = field(default_factory=lambda: defaultdict(list))
    by_bhk: dict[object, list[_PreparedLead]] = field(default_factory=lambda: defaultdict(list))
    by_property_type: dict[str, list[_PreparedLead]] = field(default_factory=lambda: defaultdict(list))


def _budget_band_keys(budget_min: float, budget_max: float) -> tuple[int, ...]:
    if budget_min <= 0 or budget_max <= 0:
        return ()
    low = int(math.floor(min(budget_min, budget_max) / _BUDGET_BAND_SIZE))
    high = int(math.floor(max(budget_min, budget_max) / _BUDGET_BAND_SIZE))
    return tuple(range(low, high + 1))


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


def _format_budget_range(min_value: object, max_value: object) -> str:
    low = _to_float(min_value, default=-1)
    high = _to_float(max_value, default=-1)
    if low < 0 or high < 0:
        return "unknown"
    if low == high:
        return str(int(low))
    return f"{int(low)}-{int(high)}"


def _eligible_leads(leads: list[StructuredLead]) -> list[StructuredLead]:
    return [
        lead
        for lead in leads
        if lead.values.get("Type") in {"Buyer", "Seller"}
        and lead.values.get("Contact Number")
        and lead.values.get("Location")
        and lead.values.get("Transaction Type")
    ]


def _prepare_match_lead(lead: StructuredLead, now: datetime) -> _PreparedLead:
    return _PreparedLead(
        lead_id=str(lead.values.get("Lead_ID", "")),
        name=str(lead.values.get("Name", "")),
        phone=str(lead.values.get("Contact Number", "")),
        lead_type=str(lead.values.get("Type", "")),
        location=str(lead.values.get("Location", "")),
        property_type=str(lead.values.get("Property Type", "")),
        bhk=lead.values.get("BHK"),
        budget_min=_to_float(lead.values.get("Budget_Min")),
        budget_max=_to_float(lead.values.get("Budget_Max")),
        transaction=str(lead.values.get("Transaction Type", "")),
        recency=recency_score(str(lead.values.get("Last Seen")), now),
        completeness=completeness_score(lead),
        confidence=_to_float(lead.values.get("Confidence Score"), 0.0) / 100,
        budget_text=_format_budget_range(lead.values.get("Budget_Min"), lead.values.get("Budget_Max")),
    )


def _seller_bucket_key(lead: _PreparedLead) -> tuple[str, str, str]:
    return (lead.transaction, lead.location, lead.property_type)


def _seller_location_key(lead: _PreparedLead) -> tuple[str, str]:
    return (lead.transaction, lead.location)


def _normalize_property_type(value: object) -> str:
    return str(value or "").strip().lower()


def _property_family(value: object) -> str:
    normalized = _normalize_property_type(value)
    if not normalized:
        return ""
    for family, members in _PROPERTY_FAMILIES.items():
        if normalized in members:
            return family
    return normalized


def _property_match_score(buyer_property: object, seller_property: object) -> float:
    buyer_normalized = _normalize_property_type(buyer_property)
    seller_normalized = _normalize_property_type(seller_property)
    if buyer_normalized and seller_normalized and buyer_normalized == seller_normalized:
        return 1.0
    if not buyer_normalized or not seller_normalized:
        return 0.35
    if _property_family(buyer_normalized) == _property_family(seller_normalized):
        return 0.75
    return 0.0


def _build_seller_buckets(sellers: list[_PreparedLead]) -> dict[tuple[str, str], _SellerBucket]:
    buckets: dict[tuple[str, str], _SellerBucket] = {}
    for seller in sellers:
        if not seller.transaction or not seller.location:
            continue
        key = _seller_location_key(seller)
        bucket = buckets.setdefault(key, _SellerBucket())
        bucket.sellers.append(seller)
        if seller.property_type:
            bucket.by_property_type[_normalize_property_type(seller.property_type)].append(seller)
        if seller.bhk is not None:
            bucket.by_bhk[seller.bhk].append(seller)
        for band in _budget_band_keys(seller.budget_min, seller.budget_max):
            bucket.by_budget_band[band].append(seller)
    return buckets


def _candidate_sellers_for_buyer(
    buyer: _PreparedLead,
    seller_buckets: dict[tuple[str, str], _SellerBucket],
) -> list[_PreparedLead]:
    if not buyer.transaction or not buyer.location:
        return []

    bucket = seller_buckets.get(_seller_location_key(buyer))
    if not bucket:
        return []

    exact_property_sellers = bucket.by_property_type.get(_normalize_property_type(buyer.property_type), [])
    if exact_property_sellers:
        bucket_sellers = exact_property_sellers
    else:
        compatible_sellers = [
            seller
            for seller in bucket.sellers
            if _property_match_score(buyer.property_type, seller.property_type) > 0.0
        ]
        bucket_sellers = compatible_sellers or bucket.sellers

    if buyer.bhk is not None:
        bhk_sellers = [seller for seller in bucket_sellers if seller.bhk == buyer.bhk]
        if bhk_sellers:
            bucket_sellers = bhk_sellers

    budget_bands = _budget_band_keys(buyer.budget_min, buyer.budget_max)
    if not budget_bands:
        return bucket_sellers

    candidates: list[_PreparedLead] = []
    seller_ids_in_bucket = {seller.lead_id for seller in bucket_sellers}
    seen_ids: set[str] = set()
    for band in budget_bands:
        for seller in bucket.by_budget_band.get(band, ()):
            if seller.lead_id in seen_ids or seller.lead_id not in seller_ids_in_bucket:
                continue
            seen_ids.add(seller.lead_id)
            candidates.append(seller)
    return candidates or bucket_sellers


def compute_matches(leads: list[StructuredLead], weights: dict[str, float], threshold: float, now: datetime) -> list[dict[str, object]]:
    prepared = [_prepare_match_lead(lead, now) for lead in _eligible_leads(leads)]
    buyers = [lead for lead in prepared if lead.lead_type == "Buyer"]
    sellers = [lead for lead in prepared if lead.lead_type == "Seller"]
    seller_buckets = _build_seller_buckets(sellers)
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
    match_date = now.date().isoformat()
    match_month = month_bucket(now)
    match_week = week_bucket(now)
    matched_at = now.isoformat(sep=" ")
    seller_static_bonus = {
        seller.lead_id: (
            seller.recency * w_recency
            + seller.completeness * w_complete
            + seller.confidence * w_conf
        ) / 2
        for seller in sellers
    }

    for buyer in buyers:
        candidate_sellers = _candidate_sellers_for_buyer(buyer, seller_buckets)
        if not candidate_sellers:
            continue

        buyer_static_bonus = (
            buyer.recency * w_recency
            + buyer.completeness * w_complete
            + buyer.confidence * w_conf
        ) / 2
        buyer_has_budget = buyer.budget_min > 0 and buyer.budget_max > 0
        top_matches: list[tuple[float, str, str, str, float, float, float, int, _PreparedLead]] = []

        for seller_index, seller in enumerate(candidate_sellers):
            property_score = _property_match_score(buyer.property_type, seller.property_type)
            if property_score <= 0.0:
                continue
            bhk_score = 1.0 if buyer.bhk and seller.bhk and buyer.bhk == seller.bhk else 0.0
            budget_score = (
                _budget_overlap(buyer.budget_min, buyer.budget_max, seller.budget_min, seller.budget_max)
                if buyer_has_budget and seller.budget_min > 0 and seller.budget_max > 0
                else 0.0
            )
            pair_recency = (buyer.recency + seller.recency) / 2
            final_score = (
                (1.0 * w_loc)
                + (property_score * w_prop)
                + (bhk_score * w_bhk)
                + (budget_score * w_budget)
                + (1.0 * w_txn)
                + buyer_static_bonus
                + seller_static_bonus[seller.lead_id]
            ) / total_w * 100

            if final_score < threshold:
                continue

            entry = (
                final_score,
                seller.lead_id or "",
                seller.phone or "",
                seller.name or "",
                bhk_score,
                budget_score,
                pair_recency,
                seller_index,
                seller,
            )
            if len(top_matches) < _MAX_MATCHES_PER_BUYER:
                heapq.heappush(top_matches, entry)
            elif final_score > top_matches[0][0]:
                heapq.heapreplace(top_matches, entry)

        if not top_matches:
            continue

        top_matches.sort(key=lambda item: item[0], reverse=True)
        for final_score, _seller_id, _seller_phone, _seller_name, bhk_score, budget_score, pair_recency, _seller_index, seller in top_matches:
            property_score = _property_match_score(buyer.property_type, seller.property_type)
            reason_parts = [f"Location: {buyer.location}", f"Property: {buyer.property_type}", f"Transaction: {buyer.transaction}"]
            if property_score < 1.0 and seller.property_type:
                reason_parts.append(f"Related property type: {seller.property_type}")
            if bhk_score == 1.0:
                reason_parts.append(f"BHK: {buyer.bhk}")
            if budget_score > 0.0:
                reason_parts.append(f"Budget overlap: {round(budget_score * 100)}%")
            if pair_recency > 0.7:
                reason_parts.append("Recent leads")

            rows.append(
                {
                    "Date": match_date,
                    "Month": match_month,
                    "Week": match_week,
                    "Buyer Lead_ID": buyer.lead_id,
                    "Buyer Name": buyer.name,
                    "Buyer Phone": buyer.phone,
                    "Seller Lead_ID": seller.lead_id,
                    "Seller Name": seller.name,
                    "Seller Phone": seller.phone,
                    "Location": buyer.location,
                    "Property Type": buyer.property_type,
                    "BHK": buyer.bhk or seller.bhk,
                    "Buyer Budget": buyer.budget_text,
                    "Seller Budget": seller.budget_text,
                    "Match Score": round(final_score, 2),
                    "Match Reason": " | ".join(reason_parts),
                    "Matched At": matched_at,
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

        lead.values["Priority Reason"] = " | ".join(reasons) if reasons else "Standard priority"


def _summary_key(lead: StructuredLead, *, include_property: bool) -> tuple[str, object, str]:
    return (
        str(lead.values.get("Location", "")),
        lead.values.get("BHK"),
        str(lead.values.get("Property Type", "")) if include_property else "",
    )


def demand_summary(leads: list[StructuredLead]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, object, str], list[StructuredLead]] = defaultdict(list)
    for lead in leads:
        if lead.values.get("Type") == "Buyer":
            grouped[_summary_key(lead, include_property=False)].append(lead)
    rows: list[dict[str, object]] = []
    for (location, bhk, _property), group in grouped.items():
        budgets = [_to_float(item.values.get("Budget_Min"), default=0) for item in group if item.values.get("Budget_Min")]
        max_budgets = [_to_float(item.values.get("Budget_Max"), default=0) for item in group if item.values.get("Budget_Max")]
        rows.append(
            {
                "Location": location,
                "BHK": bhk,
                "Budget_Min": int(min(budgets)) if budgets else "",
                "Budget_Max": int(max(max_budgets)) if max_budgets else "",
                "Count": len(group),
            }
        )
    rows.sort(key=lambda row: (-int(row["Count"]), str(row["Location"]), str(row["BHK"])))
    return rows


def supply_summary(leads: list[StructuredLead]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, object, str], list[StructuredLead]] = defaultdict(list)
    for lead in leads:
        if lead.values.get("Type") == "Seller":
            grouped[_summary_key(lead, include_property=True)].append(lead)
    rows: list[dict[str, object]] = []
    for (location, bhk, property_type), group in grouped.items():
        budgets = [_to_float(item.values.get("Budget_Min"), default=0) for item in group if item.values.get("Budget_Min")]
        max_budgets = [_to_float(item.values.get("Budget_Max"), default=0) for item in group if item.values.get("Budget_Max")]
        rows.append(
            {
                "Location": location,
                "Property Type": property_type,
                "BHK": bhk,
                "Price_Min": int(min(budgets)) if budgets else "",
                "Price_Max": int(max(max_budgets)) if max_budgets else "",
                "Count": len(group),
            }
        )
    rows.sort(key=lambda row: (-int(row["Count"]), str(row["Location"]), str(row["Property Type"]), str(row["BHK"])))
    return rows


def top_leads(leads: list[StructuredLead], count: int) -> list[StructuredLead]:
    ranked = sorted(
        [lead for lead in leads if lead.values.get("Contact Number")],
        key=lambda lead: (
            -_to_float(lead.values.get("Priority Score")),
            str(lead.values.get("Last Seen", "")),
        ),
    )
    return ranked[: max(count, 0)]
