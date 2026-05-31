from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any

APP_TIMEZONE = ZoneInfo("Asia/Kolkata")
TERMINAL_STATUSES = {"closed", "done", "converted", "lost"}
EDITABLE_FIELDS = {
    "status": "Status",
    "notes": "Notes",
    "next_follow_up": "Next Follow-up",
    "next_action": "Next Action",
    "timing": "Timing",
}


def _safe_str(value: object) -> str:
    return "" if value is None else str(value).strip()


def _now() -> datetime:
    return datetime.now(APP_TIMEZONE).replace(tzinfo=None)


def _normalize_date(value: str) -> str:
    raw = _safe_str(value)
    if not raw:
        return ""
    return date.fromisoformat(raw).isoformat()


def _compute_follow_up_pending(status: str, next_follow_up: str) -> str:
    if not next_follow_up:
        return "FALSE"
    if _safe_str(status).lower() in TERMINAL_STATUSES:
        return "FALSE"
    return "TRUE"


def _append_system_error(client, *, context: str, lead_id: str, error_type: str, error_message: str, payload: dict[str, Any]) -> None:
    timestamp = _now().isoformat(sep=" ", timespec="seconds")
    client.append_system_errors(
        [[timestamp, context, lead_id, error_type, error_message, json.dumps(payload, ensure_ascii=True)]]
    )


def save_glide_execution(
    client,
    *,
    lead_id: str,
    status: str | None = None,
    notes: str | None = None,
    next_follow_up: str | None = None,
    next_action: str | None = None,
    timing: str | None = None,
) -> dict[str, str]:
    lead_id = _safe_str(lead_id)
    if not lead_id:
        raise ValueError("lead_id is required")

    current = client.get_glide_execution_map().get(lead_id, {})
    next_values = {
        "Status": _safe_str(status) if status is not None else _safe_str(current.get("Status", "")),
        "Notes": _safe_str(notes) if notes is not None else _safe_str(current.get("Notes", "")),
        "Next Follow-up": _normalize_date(next_follow_up) if next_follow_up else "",
        "Next Action": _safe_str(next_action) if next_action is not None else _safe_str(current.get("Next Action", "")),
        "Timing": _safe_str(timing) if timing is not None else _safe_str(current.get("Timing", "")),
    }
    changed: list[tuple[str, str, str]] = []
    for api_name, column in EDITABLE_FIELDS.items():
        new_value = next_values[column]
        old_value = _safe_str(current.get(column, ""))
        if new_value != old_value:
            changed.append((column, old_value, new_value))

    now = _now()
    touch_interaction = bool(changed)
    if touch_interaction:
        next_values["Last Interaction Date"] = now.date().isoformat()
        next_values["Last Interaction Time"] = now.strftime("%H:%M")
        next_values["Last Interaction At"] = now.isoformat(sep=" ", timespec="seconds")
    else:
        next_values["Last Interaction Date"] = _safe_str(current.get("Last Interaction Date", ""))
        next_values["Last Interaction Time"] = _safe_str(current.get("Last Interaction Time", ""))
        next_values["Last Interaction At"] = _safe_str(current.get("Last Interaction At", ""))

    next_values["Follow-up Pending"] = _compute_follow_up_pending(next_values["Status"], next_values["Next Follow-up"])
    next_values["Write Sync Error"] = "FALSE"
    next_values["Updated At"] = now.isoformat(sep=" ", timespec="seconds")

    try:
        saved = client.upsert_glide_execution(lead_id, next_values)
        verified = client.get_glide_execution_map().get(lead_id, {})
        mismatch = any(_safe_str(verified.get(key, "")) != _safe_str(saved.get(key, "")) for key in next_values.keys())
        if mismatch:
            saved = client.upsert_glide_execution(lead_id, {"Write Sync Error": "TRUE"})
            _append_system_error(
                client,
                context="glide_execution_save",
                lead_id=lead_id,
                error_type="write_mismatch",
                error_message="Saved Glide execution values did not round-trip correctly.",
                payload={"expected": next_values, "verified": verified},
            )
        log_rows = [
            [now.isoformat(sep=" ", timespec="seconds"), lead_id, field, old_value, new_value, "execution_edit", saved.get("Write Sync Error", "FALSE")]
            for field, old_value, new_value in changed
        ]
        if log_rows:
            client.append_glide_write_log(log_rows)
        return saved
    except Exception as exc:
        _append_system_error(
            client,
            context="glide_execution_save",
            lead_id=lead_id,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            payload={"status": status, "notes": notes, "next_follow_up": next_follow_up, "next_action": next_action, "timing": timing},
        )
        raise


def log_glide_action(client, *, lead_id: str, action: str) -> dict[str, str]:
    lead_id = _safe_str(lead_id)
    action = _safe_str(action).lower()
    if action not in {"call", "whatsapp"}:
        raise ValueError("action must be 'call' or 'whatsapp'")

    current = client.get_glide_execution_map().get(lead_id, {})
    now = _now()
    values = {
        "Status": _safe_str(current.get("Status", "")),
        "Notes": _safe_str(current.get("Notes", "")),
        "Next Follow-up": _safe_str(current.get("Next Follow-up", "")),
        "Last Interaction Date": now.date().isoformat(),
        "Last Interaction Time": now.strftime("%H:%M"),
        "Last Interaction At": now.isoformat(sep=" ", timespec="seconds"),
        "Follow-up Pending": _compute_follow_up_pending(
            _safe_str(current.get("Status", "")),
            _safe_str(current.get("Next Follow-up", "")),
        ),
        "Write Sync Error": "FALSE",
        "Updated At": now.isoformat(sep=" ", timespec="seconds"),
    }
    try:
        saved = client.upsert_glide_execution(lead_id, values)
        client.append_glide_write_log(
            [[now.isoformat(sep=" ", timespec="seconds"), lead_id, "action", "", action, f"{action}_lead", saved.get("Write Sync Error", "FALSE")]]
        )
        return saved
    except Exception as exc:
        _append_system_error(
            client,
            context="glide_action_log",
            lead_id=lead_id,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
            payload={"action": action},
        )
        raise
