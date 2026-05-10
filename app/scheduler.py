from __future__ import annotations

from datetime import datetime
from typing import Any

from .db_client import DatabaseClient
from .glide_builder import invalidate_glide_cache
from .matcher import compute_matches, compute_priority, demand_summary, supply_summary, top_leads
from .pipeline import _load_config, _read_existing, _write_outputs
from .schemas import StructuredLead


def refresh_system(client: DatabaseClient) -> dict[str, Any]:
    """
    Scheduled refresh operation for cron-based execution.
    
    This function:
    - Recomputes matches from existing structured data
    - Recalculates priority scores
    - Updates top leads
    - Refreshes demand/supply summaries
    - Syncs clean data
    - Invalidates Glide cache
    
    Idempotent: Can be run multiple times without side effects.
    No new data ingestion: Only reprocesses existing data.
    """
    start_time = datetime.now()
    
    try:
        client.ensure_structure()
        config, weights = _load_config(client)
        now = datetime.now()
        
        # Read existing structured data
        existing = _read_existing(client)
        
        if not existing:
            return {
                "status": "SUCCESS",
                "message": "No data to refresh",
                "leads_count": 0,
                "matches_count": 0,
                "top_leads_count": 0,
                "duration_seconds": (datetime.now() - start_time).total_seconds(),
            }
        
        # Recompute matches
        matches = compute_matches(
            existing,
            weights,
            float(config.get("match_threshold", 40)),
            now
        )
        
        # Recompute priority scores
        compute_priority(existing, matches, weights, now)
        
        # Generate top leads
        top_count = int(config.get("top_leads_count", 10))
        top = top_leads(existing, top_count)
        
        # Generate summaries
        demand = demand_summary(existing)
        supply = supply_summary(existing)
        
        # Prepare validation samples
        validation_sample_size = max(0, int(config.get("validation_sample_size", 50)))
        validation_rows = existing[:validation_sample_size]
        
        match_validation_size = max(0, int(config.get("match_validation_sample_size", 15)))
        match_validation_rows = matches[:match_validation_size]
        
        top_validation_size = max(0, int(config.get("top_leads_validation_size", 10)))
        top_validation_rows = top[:top_validation_size]
        
        # Build final validation
        final_validation_rows = [
            {"Check": "Refresh Type", "Value": "Scheduled", "Status": "PASS", "Notes": "Periodic system refresh"},
            {"Check": "Total Leads", "Value": len(existing), "Status": "PASS", "Notes": "Current structured data count"},
            {"Check": "Matches Generated", "Value": len(matches), "Status": "PASS" if matches else "WARN", "Notes": "Recomputed matches"},
            {"Check": "Top Leads Generated", "Value": len(top), "Status": "PASS" if top else "WARN", "Notes": "Prioritized leads"},
            {"Check": "Demand Summary Rows", "Value": len(demand), "Status": "PASS" if demand else "WARN", "Notes": "Buyer demand groupings"},
            {"Check": "Supply Summary Rows", "Value": len(supply), "Status": "PASS" if supply else "WARN", "Notes": "Seller supply groupings"},
        ]
        
        # Write outputs (replace existing)
        _write_outputs(
            client,
            existing,
            validation_rows,
            final_validation_rows,
            matches,
            match_validation_rows,
            demand,
            supply,
            top,
            top_validation_rows,
        )
        
        # Invalidate Glide cache to force refresh
        invalidate_glide_cache()
        
        duration = (datetime.now() - start_time).total_seconds()
        
        return {
            "status": "SUCCESS",
            "message": "System refreshed successfully",
            "leads_count": len(existing),
            "matches_count": len(matches),
            "top_leads_count": len(top),
            "demand_summary_count": len(demand),
            "supply_summary_count": len(supply),
            "duration_seconds": duration,
            "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
        
    except Exception as exc:
        duration = (datetime.now() - start_time).total_seconds()
        error_msg = f"{exc.__class__.__name__}: {str(exc)}"
        
        # Log error to System Errors table
        client.append_system_errors([[
            datetime.now().isoformat(sep=" ", timespec="seconds"),
            "scheduled_refresh",
            "",
            exc.__class__.__name__,
            str(exc),
            "{}",
        ]])
        
        return {
            "status": "FAILURE",
            "message": "System refresh failed",
            "error": error_msg,
            "duration_seconds": duration,
            "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
