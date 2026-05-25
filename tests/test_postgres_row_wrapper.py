from app.db_client import _PostgresRow


def test_postgres_row_supports_mapping_helpers():
    row = _PostgresRow({"lead_id": "L-1", "name": "Alice", "phone": None})

    assert row["lead_id"] == "L-1"
    assert row[1] == "Alice"
    assert list(row.keys()) == ["lead_id", "name", "phone"]
    assert list(row.values()) == ["L-1", "Alice", None]
    assert list(row.items()) == [("lead_id", "L-1"), ("name", "Alice"), ("phone", None)]
    assert row.get("missing", "fallback") == "fallback"
    assert dict(row) == {"lead_id": "L-1", "name": "Alice", "phone": None}
