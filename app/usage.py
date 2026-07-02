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


def _get_or_create_ledger(service_client: Client, email: str) -> dict:
    response = service_client.table("usage_ledger").select("*").eq("email", email).execute()
    if response.data:
        return response.data[0]
    now = datetime.now(timezone.utc)
    insert_response = (
        service_client.table("usage_ledger")
        .insert({"email": email, "period_start": now.isoformat(), "generation_count": 0})
        .execute()
    )
    return insert_response.data[0]


def ledger_status(service_client: Client, email: str) -> dict:
    """이메일 단위로 영구 보관되는 사용량 원장에서 현재 주기 사용량을 조회한다.

    계정을 하드 삭제했다가 같은 이메일로 재가입해도 이 원장은 지워지지 않으므로
    사용 한도가 초기화되지 않는다 (profiles/generations와 별개 테이블).
    """
    ledger = _get_or_create_ledger(service_client, email)
    stored_period_start = parse_ts(ledger["period_start"])
    period_start = current_period_start(stored_period_start)
    used = ledger["generation_count"]
    if period_start != stored_period_start:
        used = 0
        service_client.table("usage_ledger").update(
            {"period_start": period_start.isoformat(), "generation_count": 0}
        ).eq("email", email).execute()
    return {"used": used, "period_start": period_start}


def record_generation(service_client: Client, email: str, used_before: int) -> None:
    service_client.table("usage_ledger").update(
        {
            "generation_count": used_before + 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("email", email).execute()
