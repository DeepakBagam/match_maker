from datetime import datetime

from app.analytics import budget_bucket, column_letter, month_bucket, week_bucket


def test_analytics_buckets():
    value = datetime(2026, 4, 3, 10, 0, 0)
    assert month_bucket(value) == "2026-04"
    assert week_bucket(value) == "2026-W14"
    assert budget_bucket(7000000, 8000000) == "30L-80L"


def test_column_letter_conversion():
    assert column_letter(1) == "A"
    assert column_letter(26) == "Z"
    assert column_letter(27) == "AA"
    assert column_letter(32) == "AF"
