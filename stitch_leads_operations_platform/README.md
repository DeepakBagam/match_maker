# Leads Mobile App

This is a separate mobile web app. It does not modify the current main app.

## What it uses

- Same backend data through proxy calls to the existing matcher app
- Same database indirectly through the existing backend
- Mobile-first screens in:
  - `main.html`
  - `code.html`

## Run

Start the existing matcher backend first, usually on `http://127.0.0.1:8000`.

Option 1: run from the repo root:

```bash
uvicorn stitch_leads_operations_platform.mobile_app:app --reload --port 8001
```

Option 2: run from inside `stitch_leads_operations_platform`:

```bash
uvicorn run_mobile:app --reload --port 8001
```

Open:

```text
http://127.0.0.1:8001
```

## Login

The mobile app has its own login screen and uses the same shared credentials as the main app.

Default credentials:
- Username: `https://www.sheltersrealty.co.in/`
- Password: `home@A1`

Override with:
- `MATCHER_AUTH_USERNAME`
- `MATCHER_AUTH_PASSWORD`

Set secure values for:
- `MOBILE_SESSION_SECRET`
- `MATCHER_INTERNAL_PROXY_TOKEN`

## Optional upstream override

If the main matcher backend is on another host or port:

```bash
set MOBILE_MATCHER_API_BASE=http://127.0.0.1:9000
uvicorn stitch_leads_operations_platform.mobile_app:app --reload --port 8001
```

If you run from inside the folder, use:

```bash
set MOBILE_MATCHER_API_BASE=http://127.0.0.1:9000
uvicorn run_mobile:app --reload --port 8001
```
