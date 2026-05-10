from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid
import traceback

from .dedup import deduplicate
from .db_client import DEFAULT_CONFIG, DEFAULT_WEIGHTS, DatabaseClient
from .extractor import MappingResolver, to_structured
from .matcher import compute_matches, compute_priority, demand_summary, supply_summary, top_leads
from .parser import filter_recent, parse_combined_whatsapp_export
from .schemas import (
    CLEAN_DATA_COLUMNS,
    FINAL_VALIDATION_COLUMNS,
    MATCH_COLUMNS,
    MATCH_REVIEW_COLUMNS,
    STRUCTURED_COLUMNS,
    SUMMARY_DEMAND_COLUMNS,
    SUMMARY_SUPPLY_COLUMNS,
    TOP_LEAD_COLUMNS,
    TOP_LEAD_REVIEW_COLUMNS,
    VALIDATION_COLUMNS,
    ParsedMessage,
    StructuredLead,
)


@dataclass
class PipelineResult:
    processed: int
    new_rows: int
    duplicates: int
    ignored: int
    matches: int
    run_id: str = ""
    start_time: str = ""
    end_time: str = ""
    status: str = "SUCCESS"
    error_message: str = ""


def _now() -> datetime:
    return datetime.now()


def _load_config(client: DatabaseClient) -> tuple[dict[str, float], dict[str, float]]:
    config = {**DEFAULT_CONFIG, **client.get_key_values("Config")}
    weights = {**DEFAULT_WEIGHTS, **client.get_key_values("Scoring Weights")}
    return config, weights


def _read_existing(client: DatabaseClient) -> list[StructuredLead]:
    table = client.read_structured()
    rows = table[1:] if table else []
    return [StructuredLead.from_row(r) for r in rows if any(str(c).strip() for c in r)]


def _parsed_message_fingerprint(msg: ParsedMessage) -> str:
    raw_text = (msg.raw_message or msg.message).strip()
    return f"{msg.source.strip()}|{msg.timestamp.isoformat(sep=' ')}|{raw_text}"


def _existing_message_fingerprints(leads: list[StructuredLead]) -> set[str]:
    fingerprints: set[str] = set()
    for lead in leads:
        source = str(lead.values.get("Source", "")).strip()
        first_seen = str(lead.values.get("First Seen", "")).strip()
        raw_message = str(lead.values.get("Raw Message", "")).strip()
        if source and first_seen and raw_message:
            fingerprints.add(f"{source}|{first_seen}|{raw_message}")
    return fingerprints


def _stored_message_fingerprints(client: DatabaseClient) -> set[str]:
    if hasattr(client, "get_processed_message_fingerprints"):
        return client.get_processed_message_fingerprints()
    rows = client.read_processed_messages()
    fingerprints: set[str] = set()
    for row in rows[1:] if rows else []:
        if not row:
            continue
        value = str(row[0]).strip()
        if value:
            fingerprints.add(value)
    return fingerprints


def _filter_new_messages(
    parsed: list[ParsedMessage],
    existing: list[StructuredLead],
    persisted_fingerprints: set[str],
) -> tuple[list[ParsedMessage], int]:
    seen = _existing_message_fingerprints(existing) | persisted_fingerprints
    filtered: list[ParsedMessage] = []
    duplicate_count = 0

    for msg in parsed:
        fingerprint = _parsed_message_fingerprint(msg)
        if fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        filtered.append(msg)

    return filtered, duplicate_count


def _source_type_counts(parsed: list[ParsedMessage]) -> tuple[int, int]:
    whatsapp = 0
    manual = 0
    for msg in parsed:
        source = str(msg.source or "").strip().lower()
        if source == "manual":
            manual += 1
        elif "whatsapp" in source:
            whatsapp += 1
    return whatsapp, manual


def _lead_source_type_counts(leads: list[StructuredLead]) -> tuple[int, int]:
    whatsapp = 0
    manual = 0
    for lead in leads:
        source = str(lead.values.get("Source", "")).strip().lower()
        if source == "manual":
            manual += 1
        elif "whatsapp" in source:
            whatsapp += 1
    return whatsapp, manual


def _status(ok: bool) -> str:
    return "PASS" if ok else "WARN"


def _build_final_validation_rows(
    latest_batch: list[ParsedMessage],
    merged: list[StructuredLead],
    matches: list[dict[str, object]],
    top: list[StructuredLead],
    demand: list[dict[str, object]],
    supply: list[dict[str, object]],
) -> list[dict[str, object]]:
    latest_whatsapp_count, latest_manual_count = _source_type_counts(latest_batch)
    dataset_whatsapp_count, dataset_manual_count = _lead_source_type_counts(merged)
    data_flow_ok = bool(merged) and dataset_whatsapp_count > 0 and dataset_manual_count > 0
    matches_ok = len(matches) > 0
    top_ok = len(top) > 0
    summaries_ok = len(demand) > 0 or len(supply) > 0

    return [
        {"Check": "Latest Batch Rows", "Value": len(latest_batch), "Status": _status(bool(latest_batch)), "Notes": "Rows processed in the most recent run"},
        {"Check": "Latest WhatsApp Rows", "Value": latest_whatsapp_count, "Status": _status(latest_whatsapp_count > 0), "Notes": "Latest batch rows from WhatsApp sources"},
        {"Check": "Latest Manual Rows", "Value": latest_manual_count, "Status": _status(latest_manual_count > 0), "Notes": "Latest batch rows from manual entry"},
        {"Check": "Dataset WhatsApp Rows", "Value": dataset_whatsapp_count, "Status": _status(dataset_whatsapp_count > 0), "Notes": "Current structured rows from WhatsApp sources"},
        {"Check": "Dataset Manual Rows", "Value": dataset_manual_count, "Status": _status(dataset_manual_count > 0), "Notes": "Current structured rows from manual entry"},
        {"Check": "Matches Generated", "Value": len(matches), "Status": _status(matches_ok), "Notes": "Usable buyer-seller matches from the current dataset"},
        {"Check": "Top Leads Generated", "Value": len(top), "Status": _status(top_ok), "Notes": "Prioritized leads available for operator action"},
        {"Check": "Demand Summary Rows", "Value": len(demand), "Status": _status(len(demand) > 0), "Notes": "Buyer demand groupings generated"},
        {"Check": "Supply Summary Rows", "Value": len(supply), "Status": _status(len(supply) > 0), "Notes": "Seller supply groupings generated"},
        {"Check": "Data Flow Check", "Value": "OK" if data_flow_ok else "Needs Review", "Status": _status(data_flow_ok), "Notes": "Requires both WhatsApp and Manual data in the current structured dataset"},
        {"Check": "Matches Usable", "Value": "OK" if matches_ok else "Needs Review", "Status": _status(matches_ok), "Notes": "At least one above-threshold match is available"},
        {"Check": "Top Leads Actionable", "Value": "OK" if top_ok else "Needs Review", "Status": _status(top_ok), "Notes": "At least one prioritized lead is available"},
        {"Check": "Summaries Accurate", "Value": "OK" if summaries_ok else "Needs Review", "Status": _status(summaries_ok), "Notes": "Demand or supply summary rows were produced from the dataset"},
    ]


def _write_outputs(
    client: DatabaseClient,
    merged: list[StructuredLead],
    validation_rows: list[StructuredLead],
    final_validation_rows: list[dict[str, object]],
    matches: list[dict[str, object]],
    match_validation_rows: list[dict[str, object]],
    demand: list[dict[str, object]],
    supply: list[dict[str, object]],
    top: list[StructuredLead],
    top_validation_rows: list[StructuredLead],
) -> None:
    """Write outputs to sheets - only replace data, don't append to avoid cell limit."""
    ignored = [lead.to_row() for lead in merged if lead.values.get("Type") == "Ignore"]
    client.replace_many_rows(
        {
            "Structured Data": (STRUCTURED_COLUMNS, [lead.to_row() for lead in merged]),
            "Rejected / Ignored Data": (STRUCTURED_COLUMNS, ignored),
            "Matches": (MATCH_COLUMNS, [[m.get(c, "") for c in MATCH_COLUMNS] for m in matches]),
            "Match Validation Checkpoint": (
                MATCH_REVIEW_COLUMNS,
                [[m.get(c, "") for c in MATCH_REVIEW_COLUMNS] for m in match_validation_rows],
            ),
            "Top Leads": (TOP_LEAD_COLUMNS, [[l.values.get(c, "") for c in TOP_LEAD_COLUMNS] for l in top]),
            "Top Leads Validation": (
                TOP_LEAD_REVIEW_COLUMNS,
                [[l.values.get(c, "") for c in TOP_LEAD_REVIEW_COLUMNS] for l in top_validation_rows],
            ),
            "Demand Summary": (
                SUMMARY_DEMAND_COLUMNS,
                [[d.get(c, "") for c in SUMMARY_DEMAND_COLUMNS] for d in demand],
            ),
            "Supply Summary": (
                SUMMARY_SUPPLY_COLUMNS,
                [[d.get(c, "") for c in SUMMARY_SUPPLY_COLUMNS] for d in supply],
            ),
            "Final Validation": (
                FINAL_VALIDATION_COLUMNS,
                [[row.get(c, "") for c in FINAL_VALIDATION_COLUMNS] for row in final_validation_rows],
            ),
            "Validation Checkpoint": (
                VALIDATION_COLUMNS,
                [[lead.values.get(c, "") for c in VALIDATION_COLUMNS] for lead in validation_rows],
            ),
        }
    )
    client.sync_clean_data_formula()


def process_parsed_messages(
    client: DatabaseClient,
    parsed: list[ParsedMessage],
    *,
    apply_lookback: bool = True,
    batch_size: int = 2000,  # Increased default
) -> PipelineResult:
    # Generate run_id and capture start time
    run_id = str(uuid.uuid4())
    start_time = datetime.now()
    start_time_str = start_time.isoformat(sep=" ", timespec="seconds")
    
    result = PipelineResult(
        processed=0,
        new_rows=0,
        duplicates=0,
        ignored=0,
        matches=0,
        run_id=run_id,
        start_time=start_time_str,
        end_time="",
        status="SUCCESS",
        error_message="",
    )
    
    try:
        client.ensure_structure()
        config, weights = _load_config(client)
        now = _now()

        filtered = parsed
        if apply_lookback:
            lookback_days = int(config.get("lookback_days", 0))
            filtered = filter_recent(parsed, lookback_days, now)

        existing = _read_existing(client)
        persisted_fingerprints = _stored_message_fingerprints(client)
        filtered, message_duplicates = _filter_new_messages(filtered, existing, persisted_fingerprints)

        location_map = MappingResolver(client.get_table("Location Mapping"))
        property_map = MappingResolver(client.get_table("Property Type Mapping"))
        
        # Process in batches to avoid memory issues with large files
        all_incoming = []
        total_messages = len(filtered)
        
        print(f"[RUN {run_id}] Processing {total_messages} messages in batches of {batch_size}...")
        
        for i in range(0, total_messages, batch_size):
            batch = filtered[i:i + batch_size]
            batch_incoming = to_structured(batch, location_map, property_map, weights)
            all_incoming.extend(batch_incoming)
            print(f"  Processed batch {i//batch_size + 1}/{(total_messages + batch_size - 1)//batch_size} ({len(batch_incoming)} leads)")
        
        incoming = all_incoming
        validation_sample_size = max(0, int(config.get("validation_sample_size", 50)))
        validation_rows = incoming[:validation_sample_size]

        deduped = deduplicate(existing, incoming, int(config.get("dedup_window_days", 1)))

        matches = compute_matches(deduped.leads, weights, float(config.get("match_threshold", 55)), now)
        match_validation_size = max(0, int(config.get("match_validation_sample_size", 15)))
        match_validation_rows = matches[:match_validation_size]
        compute_priority(deduped.leads, matches, weights, now)

        top_count = int(config.get("top_leads_count", 10))
        top = top_leads(deduped.leads, top_count)
        top_validation_size = max(0, int(config.get("top_leads_validation_size", 10)))
        top_validation_rows = top[:top_validation_size]
        demand = demand_summary(deduped.leads)
        supply = supply_summary(deduped.leads)
        final_validation_rows = _build_final_validation_rows(filtered, deduped.leads, matches, top, demand, supply)

        print(f"[RUN {run_id}] Writing results to database...")
        _write_outputs(
            client,
            deduped.leads,
            validation_rows,
            final_validation_rows,
            matches,
            match_validation_rows,
            demand,
            supply,
            top,
            top_validation_rows,
        )
        
        # Only append raw data and processed messages for NEW messages (not re-processing)
        if filtered:
            print(f"[RUN {run_id}] Appending {len(filtered)} new messages to tracking tabs...")
            
            # Append raw data in batches
            raw_rows = [[now.isoformat(sep=" "), p.source, p.sender, p.timestamp.isoformat(sep=" "), p.message] for p in filtered]
            for i in range(0, len(raw_rows), batch_size):
                batch = raw_rows[i:i + batch_size]
                client.append_rows("Raw Data", batch)
                if len(raw_rows) > batch_size:
                    print(f"  Appended raw data batch {i//batch_size + 1}/{(len(raw_rows) + batch_size - 1)//batch_size}")
            
            # Append processed messages in batches
            processed_rows = [
                [_parsed_message_fingerprint(p), p.source, p.timestamp.isoformat(sep=" "), p.raw_message or p.message]
                for p in filtered
            ]
            for i in range(0, len(processed_rows), batch_size):
                batch = processed_rows[i:i + batch_size]
                client.append_rows("Processed Messages", batch)
                if len(processed_rows) > batch_size:
                    print(f"  Appended processed messages batch {i//batch_size + 1}/{(len(processed_rows) + batch_size - 1)//batch_size}")

        from .glide_builder import invalidate_glide_cache

        invalidate_glide_cache()

        # Update result with success metrics
        result.processed = len(filtered)
        result.new_rows = deduped.new_count
        result.duplicates = message_duplicates + deduped.duplicate_count
        result.ignored = sum(1 for l in incoming if l.values.get("Type") == "Ignore")
        result.matches = len(matches)
        result.status = "SUCCESS"
        
    except Exception as exc:
        # Capture error details
        result.status = "FAILURE"
        result.error_message = f"{exc.__class__.__name__}: {str(exc)}"
        print(f"[RUN {run_id}] ERROR: {result.error_message}")
        print(traceback.format_exc())
        raise
    
    finally:
        # Always log the run, even on failure
        end_time = datetime.now()
        result.end_time = end_time.isoformat(sep=" ", timespec="seconds")
        
        # Log to Run Log table
        if hasattr(client, "append_run_log"):
            client.append_run_log([[
                result.run_id,
                result.start_time,
                result.end_time,
                result.processed,
                result.new_rows,
                result.duplicates,
                result.ignored,
                result.matches,
                result.status,
                result.error_message,
            ]])
        
        duration = (end_time - start_time).total_seconds()
        print(f"[RUN {run_id}] Completed in {duration:.2f}s - Status: {result.status}")
    
    return result


def process_whatsapp_text(client: DatabaseClient, text: str, source: str = "WhatsApp Group") -> PipelineResult:
    parsed = parse_combined_whatsapp_export(text, source)
    return process_parsed_messages(client, parsed)


def process_manual_entry(
    client: DatabaseClient,
    name: str,
    phone: str,
    requirement: str,
    location: str,
    budget: str,
    notes: str,
    source: str = "Manual",
) -> PipelineResult:
    client.ensure_structure()
    now = _now()
    msg = f"{requirement} location {location} budget {budget} notes {notes}".strip()
    sender = f"{name} {phone}".strip()

    client.append_rows(
        "Manual Entries",
        [[now.isoformat(sep=" "), name, phone, requirement, location, budget, notes, source]],
    )

    parsed = [
        ParsedMessage(
            timestamp=now,
            sender=sender,
            message=msg,
            raw_message=msg,
            source=source,
        )
    ]
    return process_parsed_messages(client, parsed, apply_lookback=False)
