from __future__ import annotations

from .config import load_settings
from .db_client import DatabaseClient


def cleanup_raw_data(client: DatabaseClient, keep_rows: int = 10000) -> None:
    for tab in ("Raw Data", "Processed Messages"):
        rows = client.get_table(tab)
        if len(rows) <= keep_rows + 1:
            continue
        header = rows[0]
        recent_rows = rows[-keep_rows:]
        client.replace_rows(tab, header, recent_rows)


def get_data_stats(client: DatabaseClient) -> dict[str, int]:
    stats: dict[str, int] = {}
    for tab in ("Raw Data", "Processed Messages", "Structured Data"):
        rows = client.get_table(tab)
        stats[tab] = max(len(rows) - 1, 0)
    return stats


def main() -> None:
    settings = load_settings()
    client = DatabaseClient(settings.database_url)
    client.ensure_structure()
    cleanup_raw_data(client)
    print(get_data_stats(client))


if __name__ == "__main__":
    main()
