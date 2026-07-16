from typing import TypedDict

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.supabase_client import get_anon_client, get_user_client
from app.timeutils import to_kst_display

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["kst"] = to_kst_display


class RedirectException(Exception):
    """세션이 없거나 승인 대기 상태일 때 지정된 경로로 리다이렉트하기 위한 예외."""

    def __init__(self, url: str):
        self.url = url


class CurrentUser(TypedDict):
    user_id: str
    email: str
    access_token: str
    profile: dict


def _fetch_profile(access_token: str, user_id: str):
    client = get_user_client(access_token)
    return client.table("profiles").select("*").eq("id", user_id).single().execute()


async def get_current_user(request: Request) -> CurrentUser:
    """세션 쿠키에서 로그인 정보를 읽고, 최신 프로필(승인 상태 포함)을 조회한다.

    Supabase의 access_token(JWT)은 기본 1시간 후 만료된다. 브라우저 세션
    쿠키 자체는 훨씬 오래 유지되므로, 폼을 오래 작성하다 access_token만
    만료된 상태로 요청을 보내면 매 요청마다 거치는 이 의존성에서 조용히
    실패해 세션 전체를 지우고 로그인 화면으로 튕겨버리는 문제가 있었다
    (그 시점에 작성 중이던 입력값이 그대로 날아감). 저장해 둔 refresh_token으로
    한 번 갱신을 시도한 뒤에도 실패하는 경우에만 로그아웃시킨다.
    """
    session = request.session
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    user_id = session.get("user_id")
    if not access_token or not user_id:
        raise RedirectException("/login")

    try:
        # supabase-py는 동기(블로킹) 클라이언트라, 이 호출을 그대로 await 없이
        # 실행하면 이 요청이 응답을 기다리는 동안 이벤트 루프 전체가 멈춰
        # 다른 사용자의 요청도 함께 지연된다. 매 요청마다 거치는 인증
        # 의존성이라 영향이 크므로 스레드풀로 넘긴다.
        response = await run_in_threadpool(_fetch_profile, access_token, user_id)
    except Exception:
        if not refresh_token:
            request.session.clear()
            raise RedirectException("/login")
        try:
            refreshed = await run_in_threadpool(
                get_anon_client().auth.refresh_session, refresh_token
            )
            access_token = refreshed.session.access_token
            session["access_token"] = access_token
            session["refresh_token"] = refreshed.session.refresh_token
            response = await run_in_threadpool(_fetch_profile, access_token, user_id)
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
