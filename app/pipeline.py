from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
import os
import uuid
import traceback

from .dedup import deduplicate
from .db_client import DEFAULT_CONFIG, DEFAULT_WEIGHTS, DatabaseClient
from .extractor import MappingResolver, to_structured
from .matcher import compute_matches, compute_priority, demand_summary, supply_summary, top_leads
from .parser import filter_recent, parse_combined_whatsapp_export
from .reference_data import REFERENCE_DATA_COLUMNS, build_reference_rows
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

ProgressReporter = Callable[[dict[str, object]], None]
_CPU_COUNT = os.cpu_count() or 1
_MAX_EXTRACT_WORKERS = max(1, int(os.getenv("MATCHLAYER_EXTRACT_THREADS", str(min(4, _CPU_COUNT)))))
_MAX_PROCESS_EXTRACT_WORKERS = max(1, int(os.getenv("MATCHLAYER_EXTRACT_PROCESSES", str(min(8, _CPU_COUNT)))))
_PARALLEL_EXTRACT_MIN_BATCHES = 2
_PARALLEL_EXTRACT_MIN_MESSAGES = 1500
_PROCESS_EXTRACT_MIN_MESSAGES = 4000
_DEFAULT_EXTRACT_BATCH_SIZE = 2000
_MEDIUM_PROCESS_BATCH_SIZE = 5000
_LARGE_PROCESS_BATCH_SIZE = 10000
_VERY_LARGE_PROCESS_BATCH_SIZE = 20000
_APPEND_TRACKING_BATCH_SIZE = 20000
_MAX_MESSAGES_PER_RUN = max(0, int(os.getenv("MATCHLAYER_MAX_MESSAGES_PER_RUN", "0")))
_DEFAULT_RETENTION_DAYS = 365

_PROCESS_LOCATION_MAP: MappingResolver | None = None
_PROCESS_PROPERTY_MAP: MappingResolver | None = None
_PROCESS_WEIGHTS: dict[str, float] | None = None


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
    if hasattr(client, "read_structured_leads_fast"):
        return client.read_structured_leads_fast()
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


def _limit_recent_messages(messages: list[ParsedMessage], max_messages: int) -> tuple[list[ParsedMessage], int]:
    if max_messages <= 0 or len(messages) <= max_messages:
        return messages, 0
    ranked = sorted(enumerate(messages), key=lambda item: (item[1].timestamp, item[0]))
    kept_pairs = ranked[-max_messages:]
    kept_pairs.sort(key=lambda item: (item[1].timestamp, item[0]))
    return [message for _, message in kept_pairs], len(messages) - max_messages


def _append_tracking_rows(
    client: DatabaseClient,
    messages: list[ParsedMessage],
    written_at: datetime,
) -> None:
    if not messages:
        return
    client.append_many_rows(
        {
            "Raw Data": [
                [written_at.isoformat(sep=" "), msg.source, msg.sender, msg.timestamp.isoformat(sep=" "), msg.message]
                for msg in messages
            ],
            "Processed Messages": [
                [_parsed_message_fingerprint(msg), msg.source, msg.timestamp.isoformat(sep=" "), msg.raw_message or msg.message]
                for msg in messages
            ],
        }
    )


def _effective_lookback_days(raw_value: object) -> int:
    try:
        parsed = int(float(raw_value or 0))
    except Exception:
        parsed = 0
    return parsed if parsed > 0 else _DEFAULT_RETENTION_DAYS


def _prune_old_tracking_data(client: DatabaseClient, now: datetime, retention_days: int) -> None:
    cutoff = now - timedelta(days=retention_days)
    previous_day = (cutoff.date() - timedelta(days=1)).isoformat()
    previous_day_end = f"{previous_day} 23:59:59"

    client.delete_rows_by_text_range("Structured Data", "Date", to_value=previous_day)
    client.delete_rows_by_text_range("Raw Data", "Timestamp", to_value=previous_day_end)
    client.delete_rows_by_text_range("Processed Messages", "Timestamp", to_value=previous_day_end)
    client.delete_rows_by_text_range("Manual Entries", "Submitted At", to_value=previous_day_end)


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
    changed_leads: list[StructuredLead],
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
    reference_rows = build_reference_rows(merged)
    if hasattr(client, "upsert_structured_leads"):
        client.upsert_structured_leads(changed_leads)
        client.replace_many_rows(
            {
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
                "Reference Data": (REFERENCE_DATA_COLUMNS, reference_rows),
            }
        )
    else:
        client.replace_many_rows(
            {
                "Structured Data": (STRUCTURED_COLUMNS, [lead.to_row() for lead in merged]),
                "Rejected / Ignored Data": (STRUCTURED_COLUMNS, [lead.to_row() for lead in merged if lead.values.get("Type") == "Ignore"]),
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
                "Reference Data": (REFERENCE_DATA_COLUMNS, reference_rows),
            }
        )
    client.sync_clean_data_formula()


def _init_extract_process_worker(
    location_map: MappingResolver,
    property_map: MappingResolver,
    weights: dict[str, float],
) -> None:
    global _PROCESS_LOCATION_MAP, _PROCESS_PROPERTY_MAP, _PROCESS_WEIGHTS
    _PROCESS_LOCATION_MAP = location_map
    _PROCESS_PROPERTY_MAP = property_map
    _PROCESS_WEIGHTS = weights


def _extract_structured_batch(
    batch: list[ParsedMessage],
    location_map: MappingResolver,
    property_map: MappingResolver,
    weights: dict[str, float],
) -> list[StructuredLead]:
    return to_structured(batch, location_map, property_map, weights)


def _extract_structured_batch_process(batch: list[ParsedMessage]) -> list[StructuredLead]:
    if _PROCESS_LOCATION_MAP is None or _PROCESS_PROPERTY_MAP is None or _PROCESS_WEIGHTS is None:
        raise RuntimeError("extract worker not initialized")
    return to_structured(batch, _PROCESS_LOCATION_MAP, _PROCESS_PROPERTY_MAP, _PROCESS_WEIGHTS)


def _recommended_extract_batch_size(total_messages: int, requested_batch_size: int) -> int:
    base = max(1, requested_batch_size)
    if total_messages >= 250000:
        return max(base, _VERY_LARGE_PROCESS_BATCH_SIZE)
    if total_messages >= 100000:
        return max(base, _LARGE_PROCESS_BATCH_SIZE)
    if total_messages >= 20000:
        return max(base, _MEDIUM_PROCESS_BATCH_SIZE)
    return base


def _extract_structured_batches(
    batches: list[list[ParsedMessage]],
    location_map: MappingResolver,
    property_map: MappingResolver,
    weights: dict[str, float],
    *,
    progress_callback: ProgressReporter | None = None,
    total_messages: int,
) -> list[StructuredLead]:
    use_process_pool = total_messages >= _PROCESS_EXTRACT_MIN_MESSAGES
    worker_cap = _MAX_PROCESS_EXTRACT_WORKERS if use_process_pool else _MAX_EXTRACT_WORKERS
    max_workers = min(worker_cap, len(batches))
    if max_workers <= 1:
        ordered_results: list[StructuredLead] = []
        processed_rows = 0
        for index, batch in enumerate(batches, start=1):
            batch_incoming = _extract_structured_batch(batch, location_map, property_map, weights)
            ordered_results.extend(batch_incoming)
            processed_rows += len(batch)
            print(f"  Processed batch {index}/{len(batches)} ({len(batch_incoming)} leads)")
            if progress_callback and total_messages:
                progress_callback(
                    {
                        "stage": "extract",
                        "stage_label": "Extracting",
                        "message": f"Extracted {min(processed_rows, total_messages)} of {total_messages} messages.",
                        "progress": 30 + int((min(processed_rows, total_messages) / total_messages) * 25),
                        "processed_rows": min(processed_rows, total_messages),
                        "total_rows": total_messages,
                    }
                )
        return ordered_results

    completed_rows = 0
    batch_results: dict[int, list[StructuredLead]] = {}
    executor_factory = ProcessPoolExecutor if use_process_pool else ThreadPoolExecutor
    executor_kwargs = {"max_workers": max_workers}
    if not use_process_pool:
        executor_kwargs["thread_name_prefix"] = "matchlayer-extract"
    if use_process_pool:
        executor_kwargs["initializer"] = _init_extract_process_worker
        executor_kwargs["initargs"] = (location_map, property_map, weights)
        with executor_factory(**executor_kwargs) as executor:
            chunk_size = max(1, min(16, len(batches) // max_workers))
            for batch_index, batch_incoming in enumerate(executor.map(_extract_structured_batch_process, batches, chunksize=chunk_size)):
                batch_results[batch_index] = batch_incoming
                completed_rows += len(batches[batch_index])
                print(f"  Processed batch {batch_index + 1}/{len(batches)} ({len(batch_incoming)} leads)")
                if progress_callback and total_messages:
                    progress_callback(
                        {
                            "stage": "extract",
                            "stage_label": "Extracting",
                            "message": f"Extracted {completed_rows} of {total_messages} messages.",
                            "progress": 30 + int((completed_rows / total_messages) * 25),
                            "processed_rows": completed_rows,
                            "total_rows": total_messages,
                        }
                    )
    else:
        with executor_factory(**executor_kwargs) as executor:
            future_map = {
                executor.submit(_extract_structured_batch, batch, location_map, property_map, weights): (index, len(batch))
                for index, batch in enumerate(batches)
            }
            for future in as_completed(future_map):
                batch_index, batch_size = future_map[future]
                batch_incoming = future.result()
                batch_results[batch_index] = batch_incoming
                completed_rows += batch_size
                print(f"  Processed batch {batch_index + 1}/{len(batches)} ({len(batch_incoming)} leads)")
                if progress_callback and total_messages:
                    progress_callback(
                        {
                            "stage": "extract",
                            "stage_label": "Extracting",
                            "message": f"Extracted {completed_rows} of {total_messages} messages.",
                            "progress": 30 + int((completed_rows / total_messages) * 25),
                            "processed_rows": completed_rows,
                            "total_rows": total_messages,
                        }
                    )

    ordered_results: list[StructuredLead] = []
    for batch_index in range(len(batches)):
        ordered_results.extend(batch_results.get(batch_index, []))
    return ordered_results


def process_parsed_messages(
    client: DatabaseClient,
    parsed: list[ParsedMessage],
    *,
    apply_lookback: bool = True,
    batch_size: int = _DEFAULT_EXTRACT_BATCH_SIZE,
    progress_callback: ProgressReporter | None = None,
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
        if progress_callback:
            progress_callback(
                {
                    "stage": "prepare",
                    "stage_label": "Preparing",
                    "message": "Preparing database structures and settings.",
                    "progress": 5,
                }
            )
        client.ensure_structure()
        config, weights = _load_config(client)
        now = _now()
        effective_lookback_days = _effective_lookback_days(config.get("lookback_days", _DEFAULT_RETENTION_DAYS))
        _prune_old_tracking_data(client, now, effective_lookback_days)

        filtered = parsed
        if apply_lookback:
            filtered = filter_recent(parsed, effective_lookback_days, now)
        truncated_messages = 0
        filtered, truncated_messages = _limit_recent_messages(filtered, _MAX_MESSAGES_PER_RUN)
        if truncated_messages:
            print(f"[RUN {run_id}] Limited workload to newest {len(filtered)} messages; skipped {truncated_messages} older messages.")
        if progress_callback:
            progress_callback(
                {
                    "stage": "dedup",
                    "stage_label": "Filtering",
                    "message": "Filtering duplicates, applying lookback rules, and limiting run size.",
                    "progress": 15,
                    "total_rows": len(filtered),
                }
            )

        existing = _read_existing(client)
        persisted_fingerprints = _stored_message_fingerprints(client)
        filtered, message_duplicates = _filter_new_messages(filtered, existing, persisted_fingerprints)

        if not filtered:
            result.processed = 0
            result.new_rows = 0
            result.duplicates = message_duplicates
            result.ignored = 0
            result.matches = 0
            if progress_callback:
                progress_callback(
                    {
                        "stage": "complete",
                        "stage_label": "Complete",
                        "message": "No new messages to process.",
                        "progress": 100,
                        "processed_rows": 0,
                        "total_rows": 0,
                        "match_count": 0,
                        "new_rows": 0,
                        "duplicates": result.duplicates,
                    }
                )
            return result

        location_map = MappingResolver(client.get_table("Location Mapping"))
        property_map = MappingResolver(client.get_table("Property Type Mapping"))
        
        # Process in batches to avoid memory issues with large files
        total_messages = len(filtered)
        effective_batch_size = _recommended_extract_batch_size(total_messages, batch_size)
        batches = [filtered[i:i + effective_batch_size] for i in range(0, total_messages, effective_batch_size)]
        use_parallel_extract = (
            total_messages >= _PARALLEL_EXTRACT_MIN_MESSAGES
            and len(batches) >= _PARALLEL_EXTRACT_MIN_BATCHES
            and min((_MAX_PROCESS_EXTRACT_WORKERS if total_messages >= _PROCESS_EXTRACT_MIN_MESSAGES else _MAX_EXTRACT_WORKERS), len(batches)) > 1
        )
        
        print(f"[RUN {run_id}] Processing {total_messages} messages in batches of {effective_batch_size}...")
        if use_parallel_extract:
            active_workers = min((_MAX_PROCESS_EXTRACT_WORKERS if total_messages >= _PROCESS_EXTRACT_MIN_MESSAGES else _MAX_EXTRACT_WORKERS), len(batches))
            pool_kind = "process" if total_messages >= _PROCESS_EXTRACT_MIN_MESSAGES else "thread"
            print(f"[RUN {run_id}] Parallel extraction enabled with {active_workers} {pool_kind} workers.")
        if progress_callback:
            progress_callback(
                {
                    "stage": "extract",
                    "stage_label": "Extracting",
                    "message": "Extracting structured leads from parsed messages.",
                    "progress": 30,
                    "total_rows": total_messages,
                    "processed_rows": 0,
                }
            )
        
        incoming = _extract_structured_batches(
            batches,
            location_map,
            property_map,
            weights,
            progress_callback=progress_callback,
            total_messages=total_messages,
        )
        validation_sample_size = max(0, int(config.get("validation_sample_size", 50)))
        validation_rows = incoming[:validation_sample_size]

        deduped = deduplicate(existing, incoming, int(config.get("dedup_window_days", 1)))

        if progress_callback:
            progress_callback(
                {
                    "stage": "match",
                    "stage_label": "Matching",
                    "message": "Computing matches, priorities, and summaries.",
                    "progress": 60,
                    "processed_rows": len(deduped.leads),
                    "total_rows": len(deduped.leads),
                }
            )
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
        if progress_callback:
            progress_callback(
                {
                    "stage": "write",
                    "stage_label": "Writing",
                    "message": "Writing structured data, matches, and reference records.",
                    "progress": 82,
                    "processed_rows": len(deduped.leads),
                    "total_rows": len(deduped.leads),
                    "match_count": len(matches),
                }
            )
        _write_outputs(
            client,
            deduped.leads,
            deduped.changed_leads,
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
            if len(filtered) <= _APPEND_TRACKING_BATCH_SIZE:
                _append_tracking_rows(client, filtered, now)
            else:
                total_tracking_batches = (len(filtered) + _APPEND_TRACKING_BATCH_SIZE - 1) // _APPEND_TRACKING_BATCH_SIZE
                for offset in range(0, len(filtered), _APPEND_TRACKING_BATCH_SIZE):
                    batch_number = (offset // _APPEND_TRACKING_BATCH_SIZE) + 1
                    _append_tracking_rows(client, filtered[offset:offset + _APPEND_TRACKING_BATCH_SIZE], now)
                    print(f"  Appended tracking batch {batch_number}/{total_tracking_batches}")

        from .glide_builder import invalidate_glide_cache

        invalidate_glide_cache()

        # Update result with success metrics
        result.processed = len(filtered)
        result.new_rows = deduped.new_count
        result.duplicates = message_duplicates + deduped.duplicate_count
        result.ignored = sum(1 for l in incoming if l.values.get("Type") == "Ignore")
        result.matches = len(matches)
        result.status = "SUCCESS"
        if progress_callback:
            progress_callback(
                {
                    "stage": "complete",
                    "stage_label": "Complete",
                    "message": "Processing complete.",
                    "progress": 100,
                    "processed_rows": result.processed,
                    "total_rows": result.processed,
                    "match_count": result.matches,
                    "new_rows": result.new_rows,
                    "duplicates": result.duplicates,
                }
            )
        
    except Exception as exc:
        # Capture error details
        result.status = "FAILURE"
        result.error_message = f"{exc.__class__.__name__}: {str(exc)}"
        print(f"[RUN {run_id}] ERROR: {result.error_message}")
        print(traceback.format_exc())
        if progress_callback:
            progress_callback(
                {
                    "stage": "failed",
                    "stage_label": "Failed",
                    "message": result.error_message,
                    "progress": 100,
                }
            )
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


def process_whatsapp_text(
    client: DatabaseClient,
    text: str,
    source: str = "WhatsApp Group",
    *,
    progress_callback: ProgressReporter | None = None,
) -> PipelineResult:
    parsed = parse_combined_whatsapp_export(text, source)
    return process_parsed_messages(client, parsed, progress_callback=progress_callback)


def process_manual_entry(
    client: DatabaseClient,
    name: str,
    phone: str,
    requirement: str,
    location: str,
    budget: str,
    notes: str,
    source: str = "Manual",
    *,
    progress_callback: ProgressReporter | None = None,
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
    return process_parsed_messages(client, parsed, apply_lookback=False, progress_callback=progress_callback)
