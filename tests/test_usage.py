from datetime import datetime, timedelta, timezone

from app.usage import current_period_start, days_until_reset, parse_ts


def test_parse_ts_handles_z_suffix():
    dt = parse_ts("2026-01-01T00:00:00.000Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026


def test_current_period_start_first_window():
    created_at = datetime.now(timezone.utc) - timedelta(days=5)
    period_start = current_period_start(created_at)
    assert period_start == created_at


def test_current_period_start_second_window():
    created_at = datetime.now(timezone.utc) - timedelta(days=35)
    period_start = current_period_start(created_at)
    assert period_start == created_at + timedelta(days=30)


def test_days_until_reset_is_non_negative():
    created_at = datetime.now(timezone.utc) - timedelta(days=29)
    period_start = current_period_start(created_at)
    assert days_until_reset(period_start) >= 0
