from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.deps import CurrentUser, require_admin, templates
from app.email_domains import is_generic_gov_email
from app.supabase_client import get_service_client
from app.usage import days_until_reset, ledger_status, set_monthly_limit
from app.config import settings

router = APIRouter(prefix="/admin")


def _admin_home_context(current: CurrentUser) -> dict:
    """관리자 화면에 필요한 데이터를 모은다. 사용자 수만큼 ledger_status()를
    순차 호출(Supabase 왕복)하므로 라우트에서 통째로 스레드풀로 넘긴다."""
    service = get_service_client()

    pending = (
        service.table("profiles")
        .select("*")
        .eq("status", "pending")
        .order("created_at")
        .execute()
        .data
    )
    # korea.kr처럼 도메인만으로 소속 기관을 특정할 수 없는 경우, 관리자가
    # 신청자가 직접 입력한 소속 학교명을 육안으로 대조해야 하므로 표시해 둔다.
    for row in pending:
        row["needs_school_check"] = is_generic_gov_email(row["email"])

    users = (
        service.table("profiles")
        .select("*")
        .neq("role", "admin")
        .order("created_at", desc=True)
        .execute()
        .data
    )

    usage_rows = []
    for user in users:
        status = ledger_status(service, user["email"])
        used = status["used"]
        limit = status["monthly_limit"]
        usage_rows.append(
            {
                "profile": user,
                "used": used,
                "limit": limit,
                "custom_limit": status["custom_limit"],
                "remaining": max(limit - used, 0),
                "reset_days": days_until_reset(status["period_start"]),
            }
        )

    return {
        "pending": pending,
        "usage_rows": usage_rows,
        "profile": current["profile"],
        "default_monthly_limit": settings.monthly_limit,
        "active_nav": "admin",
    }


@router.get("")
async def admin_home(request: Request, current: CurrentUser = Depends(require_admin)):
    context = await run_in_threadpool(_admin_home_context, current)
    return templates.TemplateResponse(request, "admin.html", context)


@router.post("/approve/{user_id}")
async def approve_user(user_id: str, current: CurrentUser = Depends(require_admin)):
    service = get_service_client()
    await run_in_threadpool(
        lambda: service.table("profiles")
        .update({"status": "approved", "approved_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", user_id)
        .execute()
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/reject/{user_id}")
async def reject_user(user_id: str, current: CurrentUser = Depends(require_admin)):
    service = get_service_client()
    await run_in_threadpool(
        lambda: service.table("profiles").update({"status": "rejected"}).eq("id", user_id).execute()
    )
    return RedirectResponse("/admin", status_code=303)


@router.post("/limit/{user_id}")
async def update_user_limit(
    user_id: str,
    monthly_limit: str = Form(""),
    current: CurrentUser = Depends(require_admin),
):
    """교사별 개별 사용 한도를 지정한다. 빈 값이면 전역 기본값을 다시 따르게 한다."""
    service = get_service_client()
    profile = await run_in_threadpool(
        lambda: service.table("profiles").select("email").eq("id", user_id).single().execute().data
    )

    value = None
    raw = monthly_limit.strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = None
        if parsed is not None and parsed >= 0:
            value = parsed

    await run_in_threadpool(set_monthly_limit, service, profile["email"], value)
    return RedirectResponse("/admin", status_code=303)
