from datetime import datetime, timedelta, timezone

from supabase import Client

from app.config import settings


def parse_ts(value: str) -> datetime:
    """Supabase가 반환하는 ISO8601 타임스탬프 문자열을 timezone-aware datetime으로 변환."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def current_period_start(created_at: datetime) -> datetime:
    """가입일 기준 30일 단위로 반복되는 현재 주기의 시작 시각을 계산한다 (달력 월 기준 아님)."""
    now = datetime.now(timezone.utc)
    elapsed_days = (now - created_at).days
    if elapsed_days < 0:
        elapsed_days = 0
    n = elapsed_days // settings.rolling_window_days
    return created_at + timedelta(days=n * settings.rolling_window_days)


def period_end(period_start: datetime) -> datetime:
    return period_start + timedelta(days=settings.rolling_window_days)


def days_until_reset(period_start: datetime) -> int:
    end = period_end(period_start)
    now = datetime.now(timezone.utc)
    remaining = end - now
    return max(remaining.days + (1 if remaining.seconds > 0 else 0), 0)


def count_usage(client: Client, user_id: str, period_start: datetime) -> int:
    response = (
        client.table("generations")
        .select("id", count="exact")
        .eq("user_id", user_id)
        .gte("created_at", period_start.isoformat())
        .execute()
    )
    return response.count or 0
