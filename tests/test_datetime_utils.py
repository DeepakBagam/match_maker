from datetime import datetime

from app.datetime_utils import parse_datetime


def test_parse_datetime_accepts_non_padded_hour():
    assert parse_datetime("2018-08-22 8:09:23") == datetime(2018, 8, 22, 8, 9, 23)


def test_parse_datetime_accepts_iso_string():
    assert parse_datetime("2026-04-08 08:09:23") == datetime(2026, 4, 8, 8, 9, 23)
