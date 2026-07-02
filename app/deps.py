from typing import TypedDict

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.supabase_client import get_user_client

templates = Jinja2Templates(directory="app/templates")


class RedirectException(Exception):
    """세션이 없거나 승인 대기 상태일 때 지정된 경로로 리다이렉트하기 위한 예외."""

    def __init__(self, url: str):
        self.url = url


class CurrentUser(TypedDict):
    user_id: str
    email: str
    access_token: str
    profile: dict


async def get_current_user(request: Request) -> CurrentUser:
    """세션 쿠키에서 로그인 정보를 읽고, 최신 프로필(승인 상태 포함)을 조회한다."""
    session = request.session
    access_token = session.get("access_token")
    user_id = session.get("user_id")
    if not access_token or not user_id:
        raise RedirectException("/login")

    client = get_user_client(access_token)
    try:
        response = client.table("profiles").select("*").eq("id", user_id).single().execute()
    except Exception:
        request.session.clear()
        raise RedirectException("/login")

    profile = response.data
    if not profile:
        request.session.clear()
        raise RedirectException("/login")

    return {
        "user_id": user_id,
        "email": session.get("email", ""),
        "access_token": access_token,
        "profile": profile,
    }


async def require_approved(request: Request) -> CurrentUser:
    current = await get_current_user(request)
    if current["profile"]["status"] != "approved":
        raise RedirectException("/pending")
    return current


async def require_admin(request: Request) -> CurrentUser:
    current = await get_current_user(request)
    if current["profile"]["role"] != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근할 수 있습니다.")
    return current
