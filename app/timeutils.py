from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def to_kst_display(iso_string: str | None) -> str:
    """Supabase가 반환하는 UTC 기준 timestamptz 문자열을 한국 시간(KST, UTC+9)
    "YYYY-MM-DD HH:MM" 형식으로 변환한다. 값이 없거나 파싱할 수 없으면
    빈 문자열을 반환한다."""
    if not iso_string:
        return ""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
    except ValueError:
        return iso_string[:16].replace("T", " ")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(KST).strftime("%Y-%m-%d %H:%M")
