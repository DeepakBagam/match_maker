# Match Maker (Python + Google Sheets / PostgreSQL)

Rule-based lead ingestion and matching pipeline for WhatsApp exports and manual leads.

## Features
- Ingest via:
  - WhatsApp `.txt` upload
  - pasted chat text
  - manual form
- Automatic processing on submit:
  - parse -> classify -> extract -> normalize -> dedup -> score -> match -> summarize
- Google Sheets as only runtime control layer for:
  - mappings
  - thresholds
  - weights
  - lookback / dedup windows
- No LLM usage. Deterministic rule-based outputs.
- **Actionable outputs**: All matches and top leads have phone numbers and are ready to call
- **Quality over quantity**: Strict eligibility filters ensure only high-quality matches appear
- **Readable explanations**: Match reasons and priority reasons in plain English

## Recent Improvements
- **Improved location extraction**: Multi-layer fallback (mapping → abbreviation → keyword → pattern)
- **Consistent transaction type**: Better rent/sale detection with budget-based inference
- **Phone number filtering**: Only leads with phones appear in matches and top leads
- **Reduced unknown budgets**: Enhanced budget extraction patterns
- **Lower match threshold**: 40 (down from 55) to produce more matches
- **Stronger recency weighting**: Recent leads prioritized heavily in matching and ranking
- **Readable match reasons**: "Location: Koregaon Park | BHK: 3 | Budget overlap: 85%"
- **Readable priority reasons**: "Very recent | Complete data | Strong match available"

See `IMPROVEMENTS.md` for detailed changes and `OPERATOR_GUIDE.md` for usage instructions.

## Project Structure
- `app/main.py`: FastAPI app + ingestion endpoints + HTML UI
- `app/pipeline.py`: orchestration of full processing flow
- `app/sheets_client.py`: Google Sheets read/write + sheet bootstrap
- `app/parser.py`: WhatsApp chat parser and recency filter
- `app/extractor.py`: classification, extraction, normalization, confidence, IDs
- `app/dedup.py`: fixed-window dedup logic
- `app/matcher.py`: matching engine, priority score, summaries
- `app/templates/index.html`: lightweight interface
- `tests/`: parser/extraction/dedup/matching tests

## Prerequisites
1. Python 3.11+
2. Google Cloud service account with Sheets + Drive access
3. A Google Spreadsheet shared with the service account email

## Environment
Copy `.env.example` values into your runtime environment.

Database:
- SQLite local default: `DATABASE_URL=sqlite:///./matchlayer.db`
- PostgreSQL on VPS: `DATABASE_URL=postgresql://matcher_user:<password>@localhost:5432/matcher_db`

Required:
- `SPREADSHEET_ID`
- `GOOGLE_CREDENTIALS_PATH`

## Install
```bash
pip install -r requirements.txt
```

## Run
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`.

## Endpoints
- `POST /ingest/whatsapp-file` (`file`, `source`)
- `POST /ingest/whatsapp-paste` (`chat_text`, `source`)
- `POST /ingest/manual` (`name`, `phone`, `requirement`, `location`, `budget`, `notes`, `source`)
- `POST /system/refresh` - Scheduled refresh endpoint for cron-based execution

Notes:
- `POST /ingest/whatsapp-file` accepts one or more `.txt` files using the same `file` field.
- For multi-file upload, each file is stored under its own source using the filename or relative folder path plus filename stem.
- Folder upload is supported from the web UI in browsers that support directory selection; a folder containing 50-60 exported `.txt` chats can be sent in one request.
- `POST /ingest/whatsapp-paste` supports multi-group combined text when each block starts with a marker such as `Source: Group Name`.

## Google Sheets Contract
The app auto-creates and seeds these tabs:
- `Raw Data`
- `Structured Data`
- `Rejected / Ignored Data`
- `Matches`
- `Match Validation Checkpoint`
- `Top Leads`
- `Top Leads Validation`
- `Demand Summary`
- `Supply Summary`
- `Final Validation`
- `Validation Checkpoint`
- `Manual Entries`
- `Clean Data`
- `Config`
- `Location Mapping`
- `Property Type Mapping`
- `Scoring Weights`

## Operator Controls (Sheet-Only)
- `Config`: `lookback_days`, `match_threshold`, `dedup_window_days`, `top_leads_count`, `validation_sample_size`, `match_validation_sample_size`, `top_leads_validation_size`
- `Location Mapping` and `Property Type Mapping`:
  - `Raw Value | Canonical Value | Aliases | Optional Tags`
- `Scoring Weights`:
  - match, confidence, and priority weights

Changes in these tabs are picked up at runtime on each ingestion call.

`Validation Checkpoint` is a latest-batch review tab for parsing and extraction QA. It stores up to `validation_sample_size` rows from the most recent processed batch so operators can review timestamps, classification, extracted fields, summaries, contact name/source, and raw vs cleaned content.

All ingested rows start with `data_status = RAW`. Operators can edit rows in `Structured Data` and mark rows `APPROVED`. `Clean Data` is a formula-driven, read-only projection of approved rows from `Structured Data`; the application does not copy values into it manually.

`Match Validation Checkpoint` stores up to `match_validation_sample_size` matches from the latest run for Milestone 2 review. `Top Leads Validation` stores up to `top_leads_validation_size` prioritized leads from the latest run.

`Final Validation` is a milestone-3 end-to-end checkpoint for the latest run. It records whether mixed-source data flowed through correctly and whether matches, top leads, and summaries were produced for operator review.

## Tests
```bash
pytest -q
```

## Scheduled Execution

The system supports cron-based scheduled execution for VPS deployment:

### Refresh Endpoint
```bash
curl -X POST http://localhost:8000/system/refresh
```

This endpoint:
- Recomputes matches from existing data
- Recalculates priority scores
- Updates top leads and summaries
- Syncs clean data
- Invalidates Glide cache

**Idempotent**: Safe to run multiple times without side effects.

### Cron Setup

Add to crontab for periodic refresh:

```bash
# Every 6 hours (recommended)
0 */6 * * * curl -X POST http://localhost:8000/system/refresh

# Daily at 2 AM
0 2 * * * curl -X POST http://localhost:8000/system/refresh

# Hourly
0 * * * * curl -X POST http://localhost:8000/system/refresh
```

See `CRON_SETUP.md` for detailed configuration and `VPS_DEPLOYMENT.md` for full deployment guide.

## Analytics Layer
This is an analytics-only layer. It must not be used as a CRM or operational view.

Connect Looker Studio only to:
- `Clean Data`
- `Matches` (for match charts only)

Do not connect any other tabs.

`Clean Data` is a BI-safe, formula-driven projection with only analytics fields. It does not expose names, phone numbers, messages, or lead IDs.

Required fields for analytics:
- `Date`
- `Month`
- `Week`
- `Location`
- `Property Type`
- `Budget Range`
- `Type`

Allowed filters:
- `Date range`
- `Location`
- `Property Type`

The dashboard contract is defined in `analytics/dashboard_spec.json` and is limited to exactly 3 dashboards with at most 4 charts each.
