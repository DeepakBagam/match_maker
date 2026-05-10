from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import (
    CLEAN_DATA_COLUMNS,
    FINAL_VALIDATION_COLUMNS,
    MANUAL_COLUMNS,
    MATCH_COLUMNS,
    MATCH_REVIEW_COLUMNS,
    RAW_COLUMNS,
    STRUCTURED_COLUMNS,
    SUMMARY_DEMAND_COLUMNS,
    SUMMARY_SUPPLY_COLUMNS,
    TOP_LEAD_COLUMNS,
    TOP_LEAD_REVIEW_COLUMNS,
    VALIDATION_COLUMNS,
)

PROCESSED_MESSAGE_COLUMNS = ["Fingerprint", "Source", "Timestamp", "Raw Message"]
RUN_LOG_COLUMNS = [
    "run_id",
    "start_time",
    "end_time",
    "rows_processed",
    "new_rows",
    "duplicates",
    "ignored",
    "matches",
    "status",
    "error_message",
]
GLIDE_EXECUTION_COLUMNS = [
    "Lead_ID",
    "Status",
    "Notes",
    "Next Follow-up",
    "Next Action",
    "Timing",
    "Last Interaction Date",
    "Last Interaction Time",
    "Last Interaction At",
    "Follow-up Pending",
    "Write Sync Error",
    "Updated At",
]
GLIDE_WRITE_LOG_COLUMNS = [
    "Timestamp",
    "Lead_ID",
    "Field",
    "Old Value",
    "New Value",
    "Event",
    "Write Sync Error",
]
SYSTEM_ERROR_COLUMNS = [
    "Timestamp",
    "Context",
    "Lead_ID",
    "Error Type",
    "Error Message",
    "Payload",
]
DEALS_LOG_COLUMNS = [
    "Timestamp",
    "Lead_ID",
    "Event_Type",
    "Deal_Status",
    "Notes",
    "Created_By",
]
ALERTS_LEADS_COLUMNS = [
    "Timestamp",
    "Contact",
    "Source",
]

REQUIRED_TABS = {
    "Raw Data": RAW_COLUMNS,
    "Processed Messages": PROCESSED_MESSAGE_COLUMNS,
    "Run Log": RUN_LOG_COLUMNS,
    "Structured Data": STRUCTURED_COLUMNS,
    "Rejected / Ignored Data": STRUCTURED_COLUMNS,
    "Matches": MATCH_COLUMNS,
    "Match Validation Checkpoint": MATCH_REVIEW_COLUMNS,
    "Top Leads": TOP_LEAD_COLUMNS,
    "Top Leads Validation": TOP_LEAD_REVIEW_COLUMNS,
    "Demand Summary": SUMMARY_DEMAND_COLUMNS,
    "Supply Summary": SUMMARY_SUPPLY_COLUMNS,
    "Final Validation": FINAL_VALIDATION_COLUMNS,
    "Manual Entries": MANUAL_COLUMNS,
    "Clean Data": CLEAN_DATA_COLUMNS,
    "Validation Checkpoint": VALIDATION_COLUMNS,
    "Glide Execution": GLIDE_EXECUTION_COLUMNS,
    "Glide Write Log": GLIDE_WRITE_LOG_COLUMNS,
    "System Errors": SYSTEM_ERROR_COLUMNS,
    "Deals Log": DEALS_LOG_COLUMNS,
    "Alerts Leads": ALERTS_LEADS_COLUMNS,
    "Config": ["key", "value"],
    "Location Mapping": ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
    "Property Type Mapping": ["Raw Value", "Canonical Value", "Aliases", "Optional Tags"],
    "Scoring Weights": ["key", "value"],
}

DEFAULT_CONFIG = {
    "lookback_days": 0,
    "match_threshold": 40,
    "dedup_window_days": 1,
    "top_leads_count": 10,
    "validation_sample_size": 50,
    "match_validation_sample_size": 15,
    "top_leads_validation_size": 10,
    "glide_activity_window_days": 120,
    "glide_recent_interaction_days": 30,
    "glide_priority_qualified_score": 60,
}

DEFAULT_WEIGHTS = {
    "confidence_location": 1,
    "confidence_budget": 1,
    "confidence_bhk": 1,
    "match_location": 2,
    "match_property": 1.5,
    "match_bhk": 1.5,
    "match_budget": 2,
    "match_transaction": 2,
    "match_recency": 2,
    "match_completeness": 1,
    "match_confidence": 1,
    "priority_recency": 2.5,
    "priority_completeness": 1.5,
    "priority_urgency": 1,
    "priority_budget": 1.5,
    "priority_match_strength": 2,
    "priority_confidence": 1,
}


class _PostgresRow:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self._values = list(payload.values())

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._payload[key]


class _PostgresCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchall(self) -> list[Any]:
        return [self._wrap_row(row) for row in self._cursor.fetchall()]

    def fetchone(self) -> Any:
        row = self._cursor.fetchone()
        return self._wrap_row(row) if row is not None else None

    @staticmethod
    def _wrap_row(row: Any) -> Any:
        if isinstance(row, dict):
            return _PostgresRow(row)
        return row


class _PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def __enter__(self) -> "_PostgresConnection":
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        return self._connection.__exit__(exc_type, exc, tb)

    def execute(self, sql: str, params: Any = None) -> _PostgresCursor:
        cursor = self._connection.execute(self._translate_sql(sql), self._normalize_params(params))
        return _PostgresCursor(cursor)

    def executemany(self, sql: str, param_sets: list[list[Any]] | list[tuple[Any, ...]]) -> _PostgresCursor:
        cursor = self._connection.cursor()
        cursor.executemany(self._translate_sql(sql), [self._normalize_params(params) for params in param_sets])
        return _PostgresCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    @staticmethod
    def _normalize_params(params: Any) -> Any:
        if params is None:
            return None
        if isinstance(params, tuple):
            return params
        if isinstance(params, list):
            return tuple(params)
        return params

    @staticmethod
    def _translate_sql(sql: str) -> str:
        return sql.replace("?", "%s")


def _table_name(tab: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", tab.lower()).strip("_")
    return f"tab_{normalized}"


def _column_name(column: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", column.lower()).strip("_")
    if not normalized:
        normalized = "col"
    if normalized[0].isdigit():
        normalized = f"c_{normalized}"
    return normalized


@dataclass
class DatabaseClient:
    database_url: str
    _structure_verified: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.backend, self.database_target = self._resolve_database_target(self.database_url)

    def _resolve_database_target(self, database_url: str) -> tuple[str, str]:
        if database_url.startswith("sqlite:///"):
            return "sqlite", database_url.removeprefix("sqlite:///")
        if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
            return "postgres", database_url
        if "://" in database_url:
            raise ValueError("Only sqlite:/// and postgresql:// DATABASE_URL values are supported")
        return "sqlite", database_url

    @property
    def is_postgres(self) -> bool:
        return self.backend == "postgres"

    def _connect(self) -> sqlite3.Connection | _PostgresConnection:
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("PostgreSQL support requires psycopg. Install dependencies from requirements.txt.") from exc
            return _PostgresConnection(psycopg.connect(self.database_target, row_factory=dict_row))

        path = Path(self.database_target)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA cache_size=-20000")
        return connection

    def ensure_structure(self, force: bool = False) -> None:
        if self._structure_verified and not force:
            return

        with self._connect() as connection:
            for tab, columns in REQUIRED_TABS.items():
                self._create_table(connection, tab, columns)
            self._seed_key_value(connection, "Config", DEFAULT_CONFIG)
            self._seed_key_value(connection, "Scoring Weights", DEFAULT_WEIGHTS)
            self._ensure_indexes(connection)
            connection.commit()

        self._structure_verified = True
        self.sync_clean_data_formula()

    def _create_table(self, connection: sqlite3.Connection, tab: str, columns: list[str]) -> None:
        table_name = _table_name(tab)
        row_id_sql = "row_id BIGSERIAL PRIMARY KEY" if self.is_postgres else "row_id INTEGER PRIMARY KEY AUTOINCREMENT"
        if columns == ["key", "value"]:
            connection.execute(
                f'CREATE TABLE IF NOT EXISTS "{table_name}" ('
                f"{row_id_sql},"
                '"key" TEXT UNIQUE NOT NULL,'
                '"value" TEXT NOT NULL)'
            )
            return

        sql_columns = [row_id_sql]
        for column in columns:
            sql_columns.append(f'"{_column_name(column)}" TEXT')
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(sql_columns)})')

    def _seed_key_value(self, connection: sqlite3.Connection, tab: str, data: dict[str, float]) -> None:
        table_name = _table_name(tab)
        count = connection.execute(f'SELECT COUNT(*) AS row_count FROM "{table_name}"').fetchone()["row_count"]
        if count:
            return
        connection.executemany(
            f'INSERT INTO "{table_name}" ("key", "value") VALUES (?, ?)',
            [(key, str(value)) for key, value in data.items()],
        )

    def _ensure_indexes(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{_table_name("Structured Data")}_date" '
            f'ON "{_table_name("Structured Data")}" ("{_column_name("Date")}")'
        )
        processed_messages_index = f'idx_{_table_name("Processed Messages")}_fingerprint'
        processed_messages_table = _table_name("Processed Messages")
        processed_messages_column = _column_name("Fingerprint")
        if self.is_postgres:
            connection.execute(f'DROP INDEX IF EXISTS "{processed_messages_index}"')
            connection.execute(
                f'CREATE INDEX IF NOT EXISTS "{processed_messages_index}" '
                f'ON "{processed_messages_table}" (md5(COALESCE("{processed_messages_column}", \'\')))'
            )
        else:
            connection.execute(
                f'CREATE INDEX IF NOT EXISTS "{processed_messages_index}" '
                f'ON "{processed_messages_table}" ("{processed_messages_column}")'
            )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{_table_name("Glide Execution")}_lead_id" '
            f'ON "{_table_name("Glide Execution")}" ("{_column_name("Lead_ID")}")'
        )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{_table_name("Matches")}_buyer_lead" '
            f'ON "{_table_name("Matches")}" ("{_column_name("Buyer Lead_ID")}")'
        )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{_table_name("Matches")}_seller_lead" '
            f'ON "{_table_name("Matches")}" ("{_column_name("Seller Lead_ID")}")'
        )

    def get_table(self, tab: str) -> list[list[str]]:
        self.ensure_structure()
        columns = REQUIRED_TABS[tab]
        header = list(columns)
        table_name = _table_name(tab)

        with self._connect() as connection:
            if columns == ["key", "value"]:
                rows = connection.execute(f'SELECT "key", "value" FROM "{table_name}" ORDER BY row_id').fetchall()
                return [header, *[[str(row["key"]), str(row["value"])] for row in rows]]

            select_columns = ", ".join(f'"{_column_name(column)}"' for column in columns)
            rows = connection.execute(f'SELECT {select_columns} FROM "{table_name}" ORDER BY row_id').fetchall()

        payload_rows: list[list[str]] = []
        for row in rows:
            payload_rows.append(["" if row[_column_name(column)] is None else str(row[_column_name(column)]) for column in columns])
        return [header, *payload_rows]

    def get_table_page(
        self,
        tab: str,
        *,
        limit: int = 200,
        offset: int = 0,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_structure()
        columns = REQUIRED_TABS[tab]
        table_name = _table_name(tab)
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        where_sql, params = self._build_date_filter(columns, from_date, to_date)

        select_columns = ", ".join(f'"{_column_name(column)}"' for column in columns)
        with self._connect() as connection:
            total = connection.execute(
                f'SELECT COUNT(*) AS row_count FROM "{table_name}" {where_sql}',
                params,
            ).fetchone()["row_count"]
            rows = connection.execute(
                f'SELECT {select_columns} FROM "{table_name}" {where_sql} ORDER BY row_id DESC LIMIT ? OFFSET ?',
                [*params, limit, offset],
            ).fetchall()

        payload_rows = []
        for row in rows:
            payload_rows.append({column: ("" if row[_column_name(column)] is None else str(row[_column_name(column)])) for column in columns})
        return {
            "columns": list(columns),
            "rows": payload_rows,
            "row_count": total,
            "page_size": limit,
            "offset": offset,
        }

    def get_table_rows(
        self,
        tab: str,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        self.ensure_structure()
        columns = REQUIRED_TABS[tab]
        table_name = _table_name(tab)
        where_sql, params = self._build_date_filter(columns, from_date, to_date)
        select_columns = ", ".join(f'"{_column_name(column)}"' for column in columns)

        with self._connect() as connection:
            rows = connection.execute(
                f'SELECT {select_columns} FROM "{table_name}" {where_sql} ORDER BY row_id DESC',
                params,
            ).fetchall()

        payload_rows = []
        for row in rows:
            payload_rows.append(
                {column: ("" if row[_column_name(column)] is None else str(row[_column_name(column)])) for column in columns}
            )
        return {
            "columns": list(columns),
            "rows": payload_rows,
            "row_count": len(payload_rows),
        }

    def _build_date_filter(
        self,
        columns: list[str],
        from_date: str | None,
        to_date: str | None,
    ) -> tuple[str, list[Any]]:
        where_parts: list[str] = []
        params: list[Any] = []
        date_column = _column_name("Date")
        if "Date" in columns and from_date:
            where_parts.append(f'"{date_column}" >= ?')
            params.append(from_date)
        if "Date" in columns and to_date:
            where_parts.append(f'"{date_column}" <= ?')
            params.append(to_date)
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return where_sql, params

    def get_key_values(self, tab: str) -> dict[str, float]:
        rows = self.get_table(tab)
        out: dict[str, float] = {}
        for row in rows[1:]:
            if len(row) < 2:
                continue
            key = row[0].strip()
            if not key:
                continue
            try:
                out[key] = float(row[1])
            except Exception:
                continue
        return out

    def update_key_values(self, tab: str, data: dict[str, object]) -> None:
        self.ensure_structure()
        table_name = _table_name(tab)
        with self._connect() as connection:
            for key, value in data.items():
                connection.execute(
                    f'INSERT INTO "{table_name}" ("key", "value") VALUES (?, ?) '
                    'ON CONFLICT("key") DO UPDATE SET "value"=excluded."value"',
                    (str(key), str(value)),
                )
            connection.commit()

    def append_rows(self, tab: str, rows: list[list[object]], batch_size: int = 1000) -> None:
        del batch_size
        if not rows:
            return
        self.ensure_structure()
        columns = REQUIRED_TABS[tab]
        table_name = _table_name(tab)
        column_names = [_column_name(column) for column in columns]
        placeholders = ", ".join("?" for _ in column_names)
        quoted_columns = ", ".join(f'"{column}"' for column in column_names)
        sql = f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})'
        values = [[("" if value is None else str(value)) for value in row[: len(columns)]] for row in rows]
        normalized = [value + [""] * (len(columns) - len(value)) for value in values]
        with self._connect() as connection:
            connection.executemany(sql, normalized)
            connection.commit()

    def replace_rows(self, tab: str, header: list[str], rows: list[list[object]]) -> None:
        del header
        self.ensure_structure()
        table_name = _table_name(tab)
        columns = REQUIRED_TABS[tab]
        column_names = [_column_name(column) for column in columns]
        placeholders = ", ".join("?" for _ in column_names)
        quoted_columns = ", ".join(f'"{column}"' for column in column_names)
        sql = f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})'
        values = [[("" if value is None else str(value)) for value in row[: len(columns)]] for row in rows]
        normalized = [value + [""] * (len(columns) - len(value)) for value in values]
        with self._connect() as connection:
            connection.execute(f'DELETE FROM "{table_name}"')
            if normalized:
                connection.executemany(sql, normalized)
            connection.commit()

    def replace_many_rows(self, payloads: dict[str, tuple[list[str], list[list[object]]]]) -> None:
        self.ensure_structure()
        with self._connect() as connection:
            for tab, (_header, rows) in payloads.items():
                table_name = _table_name(tab)
                columns = REQUIRED_TABS[tab]
                column_names = [_column_name(column) for column in columns]
                placeholders = ", ".join("?" for _ in column_names)
                quoted_columns = ", ".join(f'"{column}"' for column in column_names)
                sql = f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})'
                values = [[("" if value is None else str(value)) for value in row[: len(columns)]] for row in rows]
                normalized = [value + [""] * (len(columns) - len(value)) for value in values]
                connection.execute(f'DELETE FROM "{table_name}"')
                if normalized:
                    connection.executemany(sql, normalized)
            connection.commit()

    def sync_clean_data_formula(self) -> None:
        self.ensure_structure()
        structured_table = _table_name("Structured Data")
        clean_table = _table_name("Clean Data")
        with self._connect() as connection:
            connection.execute(f'DELETE FROM "{clean_table}"')
            approved_count = connection.execute(
                f'''
                SELECT COUNT(*) AS row_count
                FROM "{structured_table}"
                WHERE UPPER(COALESCE("{_column_name("data_status")}", '')) = 'APPROVED'
                '''
            ).fetchone()["row_count"]
            if approved_count:
                source_filter = f'UPPER(COALESCE("{_column_name("data_status")}", \'\')) = \'APPROVED\''
            else:
                source_filter = (
                    f'COALESCE("{_column_name("Type")}", \'\') IN (\'Buyer\', \'Seller\') '
                    f'AND COALESCE("{_column_name("Date")}", \'\') <> \'\''
                )
            connection.execute(
                f'''
                INSERT INTO "{clean_table}" (
                    "{_column_name("Date")}",
                    "{_column_name("Month")}",
                    "{_column_name("Week")}",
                    "{_column_name("Type")}",
                    "{_column_name("Transaction Type")}",
                    "{_column_name("Location")}",
                    "{_column_name("Property Type")}",
                    "{_column_name("BHK")}",
                    "{_column_name("Budget Range")}"
                )
                SELECT
                    "{_column_name("Date")}",
                    "{_column_name("Month")}",
                    "{_column_name("Week")}",
                    "{_column_name("Type")}",
                    "{_column_name("Transaction Type")}",
                    "{_column_name("Location")}",
                    "{_column_name("Property Type")}",
                    "{_column_name("BHK")}",
                    "{_column_name("Budget Range")}"
                FROM "{structured_table}"
                WHERE {source_filter}
                '''
            )
            connection.commit()

    def read_structured(self) -> list[list[str]]:
        return self.get_table("Structured Data")

    def read_processed_messages(self) -> list[list[str]]:
        return self.get_table("Processed Messages")

    def get_processed_message_fingerprints(self) -> set[str]:
        self.ensure_structure()
        table_name = _table_name("Processed Messages")
        fingerprint_column = _column_name("Fingerprint")
        with self._connect() as connection:
            rows = connection.execute(
                f'SELECT "{fingerprint_column}" FROM "{table_name}" WHERE COALESCE("{fingerprint_column}", \'\') <> \'\''
            ).fetchall()
        return {str(row[fingerprint_column]).strip() for row in rows if row[fingerprint_column]}

    def get_glide_execution_map(self) -> dict[str, dict[str, str]]:
        rows = self.get_table("Glide Execution")
        data: dict[str, dict[str, str]] = {}
        for row in rows[1:]:
            if not any(str(cell).strip() for cell in row):
                continue
            payload = {
                column: ("" if index >= len(row) or row[index] is None else str(row[index]))
                for index, column in enumerate(GLIDE_EXECUTION_COLUMNS)
            }
            lead_id = payload.get("Lead_ID", "").strip()
            if lead_id:
                data[lead_id] = payload
        return data

    def upsert_glide_execution(
        self,
        lead_id: str,
        values: dict[str, object],
    ) -> dict[str, str]:
        self.ensure_structure()
        table_name = _table_name("Glide Execution")
        lead_column = _column_name("Lead_ID")
        row = {
            "Lead_ID": str(lead_id).strip(),
            **{key: ("" if value is None else str(value)) for key, value in values.items()},
        }
        existing = self.get_glide_execution_map().get(row["Lead_ID"], {})
        merged = {
            column: row.get(column, existing.get(column, ""))
            for column in GLIDE_EXECUTION_COLUMNS
        }
        with self._connect() as connection:
            current = connection.execute(
                f'SELECT row_id FROM "{table_name}" WHERE "{lead_column}" = ? ORDER BY row_id DESC LIMIT 1',
                (row["Lead_ID"],),
            ).fetchone()
            if current:
                assignments = ", ".join(f'"{_column_name(column)}" = ?' for column in GLIDE_EXECUTION_COLUMNS)
                connection.execute(
                    f'UPDATE "{table_name}" SET {assignments} WHERE row_id = ?',
                    [merged.get(column, "") for column in GLIDE_EXECUTION_COLUMNS] + [current["row_id"]],
                )
            else:
                quoted_columns = ", ".join(f'"{_column_name(column)}"' for column in GLIDE_EXECUTION_COLUMNS)
                placeholders = ", ".join("?" for _ in GLIDE_EXECUTION_COLUMNS)
                connection.execute(
                    f'INSERT INTO "{table_name}" ({quoted_columns}) VALUES ({placeholders})',
                    [merged.get(column, "") for column in GLIDE_EXECUTION_COLUMNS],
                )
            connection.commit()
        return merged

    def append_glide_write_log(self, rows: list[list[object]]) -> None:
        self.append_rows("Glide Write Log", rows)

    def append_system_errors(self, rows: list[list[object]]) -> None:
        self.append_rows("System Errors", rows)

    def append_run_log(self, rows: list[list[object]]) -> None:
        self.append_rows("Run Log", rows)

    def update_structured_data_status(self, lead_id: str, new_status: str) -> None:
        """Update data_status for a specific lead in Structured Data."""
        self.ensure_structure()
        table_name = _table_name("Structured Data")
        lead_id_column = _column_name("Lead_ID")
        status_column = _column_name("data_status")
        
        with self._connect() as connection:
            connection.execute(
                f'UPDATE "{table_name}" SET "{status_column}" = ? WHERE "{lead_id_column}" = ?',
                (new_status, lead_id),
            )
            connection.commit()

    def revert_approved_to_raw_on_edit(self, lead_id: str, current_status: str) -> None:
        """Revert APPROVED data to RAW if edited."""
        if current_status.upper() == "APPROVED":
            self.update_structured_data_status(lead_id, "RAW")
