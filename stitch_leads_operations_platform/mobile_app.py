from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware


BASE_DIR = Path(__file__).resolve().parent
UPSTREAM_BASE = os.getenv("MOBILE_MATCHER_API_BASE", "http://127.0.0.1:8000").rstrip("/")
AUTH_USERNAME = os.getenv("MATCHER_AUTH_USERNAME", "https://www.sheltersrealty.co.in/")
AUTH_PASSWORD = os.getenv("MATCHER_AUTH_PASSWORD", "home@A1")
SESSION_SECRET = os.getenv("MOBILE_SESSION_SECRET", "change-me-mobile-session-secret")
INTERNAL_PROXY_TOKEN = os.getenv("MATCHER_INTERNAL_PROXY_TOKEN", "change-me-internal-proxy-token")
AUTH_EXEMPT_PATHS = {"/login", "/logout", "/health"}

app = FastAPI(title="Leads Mobile App")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")


def _request_path_with_query(request: Request) -> str:
    query = str(request.url.query or "").strip()
    return f"{request.url.path}?{query}" if query else request.url.path


def _is_authenticated_request(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def _unauthorized_response(request: Request):
    accept = request.headers.get("accept", "")
    if request.method == "GET" and "text/html" in accept:
        next_target = quote(_request_path_with_query(request), safe="/?=&")
        return RedirectResponse(url=f"/login?next={next_target}", status_code=303)
    return Response(content='{"detail":"Authentication required"}', status_code=401, media_type="application/json")


@app.middleware("http")
async def require_authentication(request: Request, call_next):
    if request.url.path in AUTH_EXEMPT_PATHS:
        return await call_next(request)
    if _is_authenticated_request(request):
        return await call_next(request)
    return _unauthorized_response(request)


@app.get("/login")
def login_page(next: str = "/") -> FileResponse:
    del next
    return FileResponse(BASE_DIR / "login.html")


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    if username.strip() == AUTH_USERNAME and password == AUTH_PASSWORD:
        request.session["authenticated"] = True
        request.session["username"] = username.strip()
        return RedirectResponse(url=next or "/", status_code=303)
    safe_next = quote(next or "/", safe="/?=&")
    return RedirectResponse(url=f"/login?next={safe_next}&error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "main.html")


@app.get("/lead")
def lead_detail() -> FileResponse:
    return FileResponse(BASE_DIR / "code.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "upstream": UPSTREAM_BASE}


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request) -> Response:
    upstream_url = f"{UPSTREAM_BASE}/{path.lstrip('/')}"
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in {"host", "content-length", "connection"}
    }
    headers["X-Internal-Auth"] = INTERNAL_PROXY_TOKEN

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                params=request.query_params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream API unavailable: {exc}") from exc

    excluded = {"content-encoding", "transfer-encoding", "connection"}
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=response_headers,
    )
