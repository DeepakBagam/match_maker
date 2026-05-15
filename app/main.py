from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import inspect
import json
from io import BytesIO
from functools import lru_cache
from pathlib import Path
import time
from typing import Any
import os
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .communication import (
    generate_buyer_match_message,
    generate_follow_up_message,
    generate_property_inquiry_message,
    generate_seller_match_message,
    generate_welcome_message,
    get_available_templates,
    get_whatsapp_url,
)
from .config import load_settings
from .data_management import clear_structured_data, get_structured_dataset, parse_optional_date
from .db_client import DatabaseClient, REQUIRED_TABS
from .glide_builder import get_glide_filter_config, get_glide_lead_detail, get_glide_readiness, get_glide_view_dataset, invalidate_glide_cache
from .glide_execution import log_glide_action, save_glide_execution
from .job_queue import get_job, submit_job, update_job_progress
from .parser import parse_combined_whatsapp_export
from .pipeline import process_manual_entry, process_parsed_messages, process_whatsapp_text
from .scheduler import refresh_system

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

AUTH_USERNAME = os.getenv("MATCHER_AUTH_USERNAME", "https://www.sheltersrealty.co.in/")
AUTH_PASSWORD = os.getenv("MATCHER_AUTH_PASSWORD", "home@A1")
SESSION_SECRET = os.getenv("MATCHER_SESSION_SECRET", "change-me-main-session-secret")
INTERNAL_PROXY_TOKEN = os.getenv("MATCHER_INTERNAL_PROXY_TOKEN", "change-me-internal-proxy-token")
AUTH_EXEMPT_PATHS = {"/login", "/logout", "/health"}

app = FastAPI(title="Match Maker")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")

_CPU_COUNT = os.cpu_count() or 1
_MAX_PARSE_WORKERS = max(1, int(os.getenv("MATCHLAYER_PARSE_WORKERS", str(min(8, _CPU_COUNT)))))
_MAX_MESSAGES_PER_RUN = max(0, int(os.getenv("MATCHLAYER_MAX_MESSAGES_PER_RUN", "0")))


def _request_path_with_query(request: Request) -> str:
    query = str(request.url.query or "").strip()
    return f"{request.url.path}?{query}" if query else request.url.path


def _is_authenticated_request(request: Request) -> bool:
    if request.headers.get("X-Internal-Auth", "") == INTERNAL_PROXY_TOKEN:
        return True
    session = request.scope.get("session")
    return bool(session and session.get("authenticated"))


def _unauthorized_response(request: Request):
    accept = request.headers.get("accept", "")
    if request.method == "GET" and "text/html" in accept:
        next_target = quote(_request_path_with_query(request), safe="/?=&")
        return RedirectResponse(url=f"/login?next={next_target}", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "Authentication required"})


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if _is_authenticated_request(request):
        return await call_next(request)
    return _unauthorized_response(request)


class ClearDataRequest(BaseModel):
    mode: str
    confirm_text: str
    from_date: str | None = None
    to_date: str | None = None


class WhatsAppMessageRequest(BaseModel):
    template_name: str
    lead_id: str = ""
    phone: str = ""


class GlideExecutionUpdateRequest(BaseModel):
    status: str = ""
    notes: str = ""
    next_follow_up: str | None = None
    next_action: str = ""
    timing: str = ""


class GlideActionRequest(BaseModel):
    action: str


class DealsLogCreateRequest(BaseModel):
    lead_id: str = ""
    event_type: str
    deal_status: str = ""
    notes: str = ""
    created_by: str = "Glide"


class AlertsLeadCreateRequest(BaseModel):
    contact: str
    source: str = "Website"
    timestamp: str | None = None
    follow_up_message: str = ""


class LeadDeleteRequest(BaseModel):
    confirm: str = ""


class StructuredDataUpdateRequest(BaseModel):
    lead_id: str
    updates: dict[str, str]


def _source_from_upload_name(filename: str, index: int) -> str:
    cleaned = (filename or "").replace("\\", "/").strip("/")
    if not cleaned:
        return f"WhatsApp Group {index}"
    parts = [part.strip() for part in cleaned.split("/") if part.strip()]
    if not parts:
        return f"WhatsApp Group {index}"
    parts[-1] = Path(parts[-1]).stem.strip() or f"WhatsApp Group {index}"
    return " / ".join(parts)


def _build_export_filename(scope: str, tab: str, from_date: str | None, to_date: str | None) -> str:
    date_suffix = ""
    if from_date or to_date:
        date_suffix = f"_{from_date or 'start'}_{to_date or 'end'}"
    base = "matchlayer_all_tabs" if scope == "all" else f"matchlayer_{tab.lower().replace(' ', '_').replace('/', '_')}"
    return f"{base}{date_suffix}.xlsx"


def _sheet_title(tab: str, used_titles: set[str]) -> str:
    cleaned = " ".join(tab.replace("/", " ").split()) or "Sheet"
    candidate = cleaned[:31]
    counter = 2
    while candidate in used_titles:
        suffix = f" {counter}"
        candidate = f"{cleaned[: 31 - len(suffix)]}{suffix}"
        counter += 1
    used_titles.add(candidate)
    return candidate


def _build_export_workbook(
    client: DatabaseClient,
    *,
    scope: str,
    tab: str,
    from_date: str | None,
    to_date: str | None,
) -> bytes:
    workbook = Workbook(write_only=True)
    used_titles: set[str] = set()
    tabs = list(REQUIRED_TABS.keys()) if scope == "all" else [tab]

    for current_tab in tabs:
        sheet = workbook.create_sheet(title=_sheet_title(current_tab, used_titles))
        dataset = client.get_table_rows(current_tab, from_date=from_date, to_date=to_date)
        columns = dataset["columns"]
        sheet.append(columns)
        for row in dataset["rows"]:
            sheet.append([row.get(column, "") for column in columns])

    stream = BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream.getvalue()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if _is_authenticated_request(request):
        return RedirectResponse(url=next or "/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "next": next, "error": ""})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    normalized_username = username.strip()
    if normalized_username == AUTH_USERNAME and password == AUTH_PASSWORD:
        request.session["authenticated"] = True
        request.session["username"] = normalized_username
        return RedirectResponse(url=next or "/", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": next or "/",
            "error": "Invalid username or password",
        },
        status_code=401,
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@lru_cache(maxsize=1)
def _client() -> DatabaseClient:
    settings = load_settings()
    client = DatabaseClient(settings.database_url)
    client.ensure_structure()
    return client


def _translate_ingest_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="Database access denied.")
    return HTTPException(status_code=500, detail=str(exc))


def _normalize_row_timestamp(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now().isoformat(sep=" ", timespec="seconds")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="timestamp must be ISO-8601 date or datetime") from exc
    if raw and len(raw) == 10:
        parsed = datetime.combine(parsed.date(), datetime.min.time())
    return parsed.isoformat(sep=" ", timespec="seconds")


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _job_progress_reporter(job_id: str):
    def _report(payload: dict[str, object]) -> None:
        normalized = dict(payload)
        if "progress" in normalized:
            try:
                normalized["progress"] = max(0, min(100, int(float(normalized["progress"]))))
            except Exception:
                normalized["progress"] = 0
        update_job_progress(job_id, **normalized)

    return _report


def _process_parsed_messages_with_progress(client, parsed_messages, progress_callback=None):
    if progress_callback and "progress_callback" in inspect.signature(process_parsed_messages).parameters:
        return process_parsed_messages(client, parsed_messages, progress_callback=progress_callback)
    return process_parsed_messages(client, parsed_messages)


def _process_manual_entry_with_progress(
    client,
    *,
    name: str,
    phone: str,
    requirement: str,
    location: str,
    budget: str,
    notes: str,
    source: str,
    progress_callback=None,
):
    if progress_callback and "progress_callback" in inspect.signature(process_manual_entry).parameters:
        return process_manual_entry(
            client,
            name=name,
            phone=phone,
            requirement=requirement,
            location=location,
            budget=budget,
            notes=notes,
            source=source,
            progress_callback=progress_callback,
        )
    return process_manual_entry(
        client,
        name=name,
        phone=phone,
        requirement=requirement,
        location=location,
        budget=budget,
        notes=notes,
        source=source,
    )


def _parse_upload_payload(
    index: int,
    total_files: int,
    filename: str,
    content: bytes,
    source: str,
) -> tuple[int, str, float, list[Any]]:
    if not filename.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="Only .txt WhatsApp export files are supported")

    file_size_mb = len(content) / (1024 * 1024)
    text = content.decode("utf-8", errors="ignore")
    file_source = source.strip()
    if total_files > 1 or not file_source:
        file_source = _source_from_upload_name(filename, index)
    parsed_messages = parse_combined_whatsapp_export(text, file_source)
    return index, filename, file_size_mb, parsed_messages


def _parse_uploaded_messages(
    upload_payloads: list[tuple[str, bytes]],
    source: str,
    *,
    progress_callback=None,
) -> list[Any]:
    parsed_messages: list[Any] = []
    total_size = 0.0
    large_files: list[tuple[str, float]] = []
    total_files = len(upload_payloads)

    if progress_callback:
        progress_callback(
            {
                "stage": "parse",
                "stage_label": "Parsing",
                "message": f"Parsing {total_files} uploaded file{'s' if total_files != 1 else ''}.",
                "progress": 12,
                "processed_rows": 0,
                "total_rows": total_files,
            }
        )

    indexed_payloads = [
        (index, total_files, filename, content, source)
        for index, (filename, content) in enumerate(upload_payloads, start=1)
    ]
    ordered_results: dict[int, tuple[str, float, list[Any]]] = {}
    completed_files = 0

    if total_files > 1:
        max_workers = min(total_files, _MAX_PARSE_WORKERS)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="matchlayer-parse") as executor:
            future_map = {
                executor.submit(_parse_upload_payload, index, file_count, filename, content, upload_source): index
                for index, file_count, filename, content, upload_source in indexed_payloads
            }
            for future in as_completed(future_map):
                index, filename, file_size_mb, batch_messages = future.result()
                ordered_results[index] = (filename, file_size_mb, batch_messages)
                completed_files += 1
                total_size += file_size_mb
                if file_size_mb > 10:
                    large_files.append((filename, file_size_mb))
                if progress_callback:
                    progress_callback(
                        {
                            "stage": "parse",
                            "stage_label": "Parsing",
                            "message": f"Parsed file {completed_files} of {total_files}: {filename}",
                            "progress": 12 + int((completed_files / max(total_files, 1)) * 13),
                            "processed_rows": completed_files,
                            "total_rows": total_files,
                        }
                    )
    else:
        index, file_count, filename, content, upload_source = indexed_payloads[0]
        parsed = _parse_upload_payload(index, file_count, filename, content, upload_source)
        ordered_results[parsed[0]] = (parsed[1], parsed[2], parsed[3])
        total_size = parsed[2]
        if parsed[2] > 10:
            large_files.append((parsed[1], parsed[2]))
        if progress_callback:
            progress_callback(
                {
                    "stage": "parse",
                    "stage_label": "Parsing",
                    "message": f"Parsed file 1 of {total_files}: {parsed[1]}",
                    "progress": 25,
                    "processed_rows": 1,
                    "total_rows": total_files,
                }
            )

    for index in sorted(ordered_results):
        parsed_messages.extend(ordered_results[index][2])

    if large_files:
        print(f"\n[WARN] Processing {len(large_files)} large file(s):")
        for filename, size in large_files:
            print(f"  - {filename}: {size:.1f} MB")
        print(f"Total size: {total_size:.1f} MB")
        print(f"Total messages: {len(parsed_messages)}")
        print("This may take several minutes...\n")

    if len(parsed_messages) > 100000:
        print(f"\n[WARN] Very large upload: {len(parsed_messages)} messages")
        if _MAX_MESSAGES_PER_RUN > 0:
            print(f"Only the most recent {_MAX_MESSAGES_PER_RUN:,} messages will be processed in this run.")
        print("Consider splitting into smaller batches for better performance.\n")

    return parsed_messages


def _process_whatsapp_upload_job(
    upload_payloads: list[tuple[str, bytes]],
    source: str,
    *,
    progress_callback=None,
) -> dict[str, Any]:
    parsed_messages = _parse_uploaded_messages(upload_payloads, source, progress_callback=progress_callback)
    return _process_parsed_messages_with_progress(_client(), parsed_messages, progress_callback).__dict__


def _process_whatsapp_paste_job(chat_text: str, source: str, *, progress_callback=None) -> dict[str, Any]:
    if progress_callback:
        progress_callback(
            {
                "stage": "parse",
                "stage_label": "Parsing",
                "message": "Parsing pasted chat text.",
                "progress": 20,
            }
        )
    parsed_messages = parse_combined_whatsapp_export(chat_text, source)
    return _process_parsed_messages_with_progress(_client(), parsed_messages, progress_callback).__dict__


def _process_manual_job(
    *,
    name: str,
    phone: str,
    requirement: str,
    location: str,
    budget: str,
    notes: str,
    source: str,
    progress_callback=None,
) -> dict[str, Any]:
    if progress_callback:
        progress_callback(
            {
                "stage": "parse",
                "stage_label": "Parsing",
                "message": "Preparing manual lead entry.",
                "progress": 20,
                "processed_rows": 1,
                "total_rows": 1,
            }
        )
    return _process_manual_entry_with_progress(
        _client(),
        name=name,
        phone=phone,
        requirement=requirement,
        location=location,
        budget=budget,
        notes=notes,
        source=source,
        progress_callback=progress_callback,
    ).__dict__


@app.post("/ingest/whatsapp-file")
async def ingest_whatsapp_file(
    file: list[UploadFile] = File(...),
    source: str = Form(default=""),
    background: str = Form(default="false"),
):
    uploads = [item for item in file if item.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="At least one .txt WhatsApp export file is required")

    upload_payloads: list[tuple[str, bytes]] = []
    for upload in uploads:
        if not upload.filename.lower().endswith(".txt"):
            raise HTTPException(status_code=400, detail="Only .txt WhatsApp export files are supported")
        content = await upload.read()
        upload_payloads.append((upload.filename, content))

    if _is_truthy(background):
        job = submit_job(
            "whatsapp-file",
            lambda job_id, payloads=upload_payloads, upload_source=source.strip(): _process_whatsapp_upload_job(
                payloads,
                upload_source,
                progress_callback=_job_progress_reporter(job_id),
            ),
        )
        return JSONResponse(
            status_code=202,
            content={
                "message": "Background processing started. You can continue using Data and Glide while ingestion runs.",
                "job_id": job["job_id"],
                "status": job["status"],
                "kind": job["kind"],
            },
        )

    try:
        return await asyncio.to_thread(_process_whatsapp_upload_job, upload_payloads, source.strip())
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/ingest/whatsapp-paste")
async def ingest_whatsapp_paste(
    chat_text: str = Form(...),
    source: str = Form(default="WhatsApp Group"),
    background: str = Form(default="false"),
):
    if not chat_text.strip():
        raise HTTPException(status_code=400, detail="chat_text is required")

    if _is_truthy(background):
        job = submit_job(
            "whatsapp-paste",
            lambda job_id, payload_text=chat_text, payload_source=source.strip() or "WhatsApp Group": _process_whatsapp_paste_job(
                payload_text,
                payload_source,
                progress_callback=_job_progress_reporter(job_id),
            ),
        )
        return JSONResponse(
            status_code=202,
            content={
                "message": "Background processing started. You can continue using Data and Glide while ingestion runs.",
                "job_id": job["job_id"],
                "status": job["status"],
                "kind": job["kind"],
            },
        )

    try:
        return await asyncio.to_thread(_process_whatsapp_paste_job, chat_text, source.strip() or "WhatsApp Group")
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/ingest/manual")
async def ingest_manual(
    name: str = Form(...),
    phone: str = Form(default=""),
    requirement: str = Form(...),
    location: str = Form(default=""),
    budget: str = Form(default=""),
    notes: str = Form(default=""),
    source: str = Form(default="Manual"),
    background: str = Form(default="false"),
):
    if not name.strip() or not requirement.strip():
        raise HTTPException(status_code=400, detail="name and requirement are required")

    if _is_truthy(background):
        job = submit_job(
            "manual-entry",
            lambda job_id: _process_manual_job(
                name=name.strip(),
                phone=phone.strip(),
                requirement=requirement.strip(),
                location=location.strip(),
                budget=budget.strip(),
                notes=notes.strip(),
                source=source.strip() or "Manual",
                progress_callback=_job_progress_reporter(job_id),
            ),
        )
        return JSONResponse(
            status_code=202,
            content={
                "message": "Background processing started. You can continue using Data and Glide while ingestion runs.",
                "job_id": job["job_id"],
                "status": job["status"],
                "kind": job["kind"],
            },
        )

    try:
        return await asyncio.to_thread(
            _process_manual_job,
            name=name.strip(),
            phone=phone.strip(),
            requirement=requirement.strip(),
            location=location.strip(),
            budget=budget.strip(),
            notes=notes.strip(),
            source=source.strip() or "Manual",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/jobs/{job_id}")
def get_job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
    return job


@app.get("/jobs/{job_id}/events")
def stream_job_status(job_id: str):
    if get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")

    def event_stream():
        last_version = -1
        while True:
            job = get_job(job_id)
            if job is None:
                yield "event: error\ndata: " + json.dumps({"detail": f"Unknown job: {job_id}"}) + "\n\n"
                break
            version = int(job.get("version", 0) or 0)
            if version != last_version:
                last_version = version
                yield "event: progress\ndata: " + json.dumps(job) + "\n\n"
            if job.get("status") in {"success", "failure"}:
                break
            time.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/data")
def get_data(
    tab: str = "Structured Data",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    try:
        parsed_from = parse_optional_date(from_date)
        parsed_to = parse_optional_date(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {exc}") from exc

    if tab not in REQUIRED_TABS:
        raise HTTPException(status_code=400, detail=f"Unknown tab: {tab}")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")

    try:
        client = _client()
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc

    payload = get_structured_dataset(
        client,
        tab=tab,
        from_date=parsed_from,
        to_date=parsed_to,
        limit=limit,
        offset=offset,
    )
    payload["available_tabs"] = list(REQUIRED_TABS.keys())
    return payload


@app.get("/data/export")
def export_data(
    tab: str = "Structured Data",
    from_date: str | None = None,
    to_date: str | None = None,
    scope: str = "tab",
):
    try:
        parsed_from = parse_optional_date(from_date)
        parsed_to = parse_optional_date(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {exc}") from exc

    if scope not in {"tab", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'tab' or 'all'")
    if tab not in REQUIRED_TABS:
        raise HTTPException(status_code=400, detail=f"Unknown tab: {tab}")
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")

    try:
        content = _build_export_workbook(
            _client(),
            scope=scope,
            tab=tab,
            from_date=parsed_from.isoformat() if parsed_from else None,
            to_date=parsed_to.isoformat() if parsed_to else None,
        )
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc

    filename = _build_export_filename(scope, tab, from_date, to_date)
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/data/sync")
def sync_data():
    try:
        dataset = get_structured_dataset(_client())
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
    return {
        "message": "The database is the live data source. No external sync is required.",
        "row_count": dataset["row_count"],
    }


@app.get("/glide/view")
def glide_view(
    mode: str = "today",
    search: str = "",
    lead_type: str = "",
    property_type: str = "",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    try:
        parsed_from = parse_optional_date(from_date)
        parsed_to = parse_optional_date(to_date)
        if parsed_from and parsed_to and parsed_from > parsed_to:
            raise HTTPException(status_code=400, detail="from_date cannot be after to_date")
        payload = get_glide_view_dataset(
            _client(),
            mode=mode,
            search=search,
            lead_type=lead_type,
            property_type=property_type,
            from_date=parsed_from,
            to_date=parsed_to,
            limit=limit,
            offset=offset,
        )
        payload["filters"] = {
            "from_date": parsed_from.isoformat() if parsed_from else None,
            "to_date": parsed_to.isoformat() if parsed_to else None,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
    return payload


@app.get("/glide/view/{lead_id}")
def glide_view_detail(lead_id: str):
    try:
        payload = get_glide_lead_detail(_client(), lead_id)
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Unknown glide lead: {lead_id}")
    return payload


@app.post("/glide/view/{lead_id}/execution")
def glide_view_update_execution(lead_id: str, payload: GlideExecutionUpdateRequest):
    try:
        save_glide_execution(
            _client(),
            lead_id=lead_id,
            status=payload.status,
            notes=payload.notes,
            next_follow_up=payload.next_follow_up,
            next_action=payload.next_action,
            timing=payload.timing,
        )
        invalidate_glide_cache()
        detail = get_glide_lead_detail(_client(), lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown glide lead: {lead_id}")
    return {"message": "Glide execution updated successfully", "detail": detail}


@app.post("/glide/view/{lead_id}/action")
def glide_view_log_action(lead_id: str, payload: GlideActionRequest):
    try:
        log_glide_action(_client(), lead_id=lead_id, action=payload.action)
        invalidate_glide_cache()
        detail = get_glide_lead_detail(_client(), lead_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown glide lead: {lead_id}")
    return {"message": "Glide action logged successfully", "detail": detail}


@app.get("/glide/readiness")
def glide_readiness():
    try:
        return get_glide_readiness(_client())
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/glide/filter-config")
def glide_filter_config():
    try:
        return get_glide_filter_config(_client())
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/search")
def search_reference_data(
    query: str = "",
    entry_type: str = "",
    lead_type: str = "",
    location: str = "",
    property_type: str = "",
    broker: str = "",
    phone: str = "",
    limit: int = 50,
    offset: int = 0,
    include_filters: bool = False,
):
    try:
        client = _client()
        payload = client.search_reference_data(
            query=query,
            entry_type=entry_type,
            lead_type=lead_type,
            location=location,
            property_type=property_type,
            broker=broker,
            phone=phone,
            limit=limit,
            offset=offset,
        )
        payload["filters"] = {
            "query": query,
            "entry_type": entry_type,
            "lead_type": lead_type,
            "location": location,
            "property_type": property_type,
            "broker": broker,
            "phone": phone,
        }
        if include_filters:
            payload["filter_options"] = client.get_reference_filter_options()
        return payload
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/search/options")
def search_reference_options():
    try:
        return _client().get_reference_filter_options()
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/glide/deals-log")
def glide_deals_log(limit: int = 25, offset: int = 0, from_date: str | None = None, to_date: str | None = None):
    try:
        parsed_from = parse_optional_date(from_date)
        parsed_to = parse_optional_date(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {exc}") from exc
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")
    try:
        return _client().get_table_page(
            "Deals Log",
            limit=limit,
            offset=offset,
            from_date=parsed_from.isoformat() if parsed_from else None,
            to_date=parsed_to.isoformat() if parsed_to else None,
        )
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/glide/deals-log")
def glide_create_deals_log(payload: DealsLogCreateRequest):
    lead_id = payload.lead_id.strip()
    event_type = payload.event_type.strip()
    if not event_type:
        raise HTTPException(status_code=400, detail="event_type is required")
    try:
        _client().append_rows(
            "Deals Log",
            [[
                datetime.now().isoformat(sep=" ", timespec="seconds"),
                lead_id,
                event_type,
                payload.deal_status.strip(),
                payload.notes.strip(),
                payload.created_by.strip() or "Glide",
            ]],
        )
        invalidate_glide_cache()
        return {"message": "Deals log entry created successfully"}
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/glide/alerts-leads")
def glide_alerts_leads(limit: int = 25, offset: int = 0, from_date: str | None = None, to_date: str | None = None):
    try:
        parsed_from = parse_optional_date(from_date)
        parsed_to = parse_optional_date(to_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date filter: {exc}") from exc
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")
    try:
        return _client().get_table_page(
            "Alerts Leads",
            limit=limit,
            offset=offset,
            from_date=parsed_from.isoformat() if parsed_from else None,
            to_date=parsed_to.isoformat() if parsed_to else None,
        )
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/glide/alerts-leads")
def glide_create_alerts_lead(payload: AlertsLeadCreateRequest):
    contact = payload.contact.strip()
    if not contact:
        raise HTTPException(status_code=400, detail="contact is required")
    try:
        _client().append_rows(
            "Alerts Leads",
            [[
                _normalize_row_timestamp(payload.timestamp),
                contact,
                payload.source.strip() or "Website",
                payload.follow_up_message.strip(),
            ]],
        )
        return {"message": "Alerts lead captured successfully"}
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.delete("/glide/view/{lead_id}")
def glide_delete_lead(lead_id: str, payload: LeadDeleteRequest):
    normalized_lead_id = lead_id.strip()
    if not normalized_lead_id:
        raise HTTPException(status_code=400, detail="lead_id is required")
    if payload.confirm.strip().upper() != "DELETE":
        raise HTTPException(status_code=400, detail="Type DELETE to confirm lead deletion")

    try:
        result = _client().delete_lead(normalized_lead_id)
        invalidate_glide_cache()
        return {
            "message": "Lead deleted successfully",
            "lead_id": normalized_lead_id,
            "deleted": result,
        }
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/communication/templates")
def get_communication_templates():
    """Get available WhatsApp message templates."""
    try:
        return {"templates": get_available_templates()}
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/communication/generate-message")
def generate_communication_message(payload: WhatsAppMessageRequest):
    """Generate WhatsApp message from template for a lead."""
    try:
        client = _client()
        
        # Get lead detail if lead_id provided
        lead_detail = None
        if payload.lead_id:
            from .glide_builder import get_glide_lead_detail
            lead_detail = get_glide_lead_detail(client, payload.lead_id)
            if not lead_detail:
                raise HTTPException(status_code=404, detail=f"Lead not found: {payload.lead_id}")
        
        # Generate message based on template
        if payload.template_name == "buyer_match_notification" and lead_detail:
            message = generate_buyer_match_message(
                {"name": lead_detail.get("name", "")},
                {
                    "location": lead_detail.get("area", ""),
                    "property_type": lead_detail.get("match_1_property_summary", "").split("|")[0].strip() if lead_detail.get("match_1_property_summary") else "",
                    "bhk": lead_detail.get("bhk", ""),
                    "budget": lead_detail.get("budget", ""),
                    "broker_name": lead_detail.get("match_1_broker_name", ""),
                    "broker_phone": lead_detail.get("match_1_broker_phone", ""),
                    "match_reason": lead_detail.get("match_reason", ""),
                }
            )
        elif payload.template_name == "follow_up_reminder" and lead_detail:
            message = generate_follow_up_message(
                {
                    "name": lead_detail.get("name", ""),
                    "location": lead_detail.get("area", ""),
                },
                {
                    "status": lead_detail.get("status", ""),
                    "notes": lead_detail.get("notes", ""),
                }
            )
        elif payload.template_name == "welcome_message" and lead_detail:
            message = generate_welcome_message({
                "name": lead_detail.get("name", ""),
                "location": lead_detail.get("area", ""),
                "property_type": "",
                "bhk": lead_detail.get("bhk", ""),
                "budget": lead_detail.get("budget", ""),
            })
        else:
            raise HTTPException(status_code=400, detail="Invalid template or missing lead data")
        
        # Generate WhatsApp URL if phone provided
        phone = payload.phone or (lead_detail.get("phone", "") if lead_detail else "")
        whatsapp_url = get_whatsapp_url(phone, message) if phone else ""
        
        return {
            "message": message,
            "whatsapp_url": whatsapp_url,
            "phone": phone,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/data/update-structured")
def update_structured_data(payload: StructuredDataUpdateRequest):
    """Update structured data row and revert APPROVED to RAW if edited."""
    try:
        client = _client()
        
        # Get current data status
        structured_data = client.get_table("Structured Data")
        header = structured_data[0] if structured_data else []
        rows = structured_data[1:] if len(structured_data) > 1 else []
        
        lead_id_idx = header.index("Lead_ID") if "Lead_ID" in header else -1
        status_idx = header.index("data_status") if "data_status" in header else -1
        
        if lead_id_idx < 0:
            raise HTTPException(status_code=400, detail="Lead_ID column not found")
        
        # Find the lead and check status
        current_status = ""
        for row in rows:
            if len(row) > lead_id_idx and row[lead_id_idx] == payload.lead_id:
                if status_idx >= 0 and len(row) > status_idx:
                    current_status = str(row[status_idx]).strip()
                break
        
        # If APPROVED, revert to RAW
        if current_status.upper() == "APPROVED":
            client.revert_approved_to_raw_on_edit(payload.lead_id, current_status)
            return {
                "message": "Data updated and status reverted from APPROVED to RAW",
                "lead_id": payload.lead_id,
                "previous_status": current_status,
                "new_status": "RAW",
            }
        
        return {
            "message": "Data updated",
            "lead_id": payload.lead_id,
            "status": current_status or "RAW",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.get("/data/run-log")
def get_run_log(limit: int = 50, offset: int = 0):
    """Get run log entries."""
    try:
        return _client().get_table_page("Run Log", limit=limit, offset=offset)
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc


@app.post("/data/clear")
def clear_data(payload: ClearDataRequest):
    if payload.confirm_text.strip().upper() != "CONFIRM":
        raise HTTPException(status_code=400, detail="Type CONFIRM to enable deletion")

    try:
        parsed_from = parse_optional_date(payload.from_date)
        parsed_to = parse_optional_date(payload.to_date)
        result = clear_structured_data(_client(), payload.mode, parsed_from, parsed_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc

    return {
        "message": "Data cleared successfully",
        "result": result.__dict__,
        "warning": "This action is irreversible",
    }


@app.get("/settings")
def get_settings():
    """Get current settings from database Config table."""
    try:
        client = _client()
        client.ensure_structure()
        config = client.get_key_values("Config")
        return {
            "match_threshold": config.get("match_threshold", 40),
            "dedup_window_days": config.get("dedup_window_days", 1),
            "lookback_days": config.get("lookback_days", 365),
            "top_leads_count": config.get("top_leads_count", 10),
            "top_leads_validation_size": config.get("top_leads_validation_size", 10),
            "glide_activity_window_days": config.get("glide_activity_window_days", 120),
            "glide_recent_interaction_days": config.get("glide_recent_interaction_days", 30),
            "glide_priority_qualified_score": config.get("glide_priority_qualified_score", 60),
        }
    except Exception as exc:
        import traceback
        print(f"Settings error: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/settings")
async def update_settings(request: Request):
    """Update settings in database Config table."""
    try:
        settings = await request.json()
        client = _client()
        client.ensure_structure()
        client.update_key_values("Config", settings)
        invalidate_glide_cache()
        return {"message": "Settings updated successfully", "updated": settings}
    except Exception as exc:
        import traceback
        print(f"Update settings error: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/system/refresh")
def system_refresh():
    """Scheduled refresh endpoint for cron-based execution.
    
    Idempotent operation that:
    - Recomputes matches from existing data
    - Recalculates priority scores
    - Updates top leads and summaries
    - Syncs clean data
    - Invalidates Glide cache
    
    Safe to run multiple times without side effects.
    No new data ingestion - only reprocesses existing data.
    
    Example cron usage:
    # Refresh every 6 hours
    0 */6 * * * curl -X POST http://localhost:8000/system/refresh
    
    # Refresh daily at 2 AM
    0 2 * * * curl -X POST http://localhost:8000/system/refresh
    """
    try:
        result = refresh_system(_client())
        return result
    except Exception as exc:
        raise _translate_ingest_error(exc) from exc
