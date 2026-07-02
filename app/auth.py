from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.deps import CurrentUser, get_current_user
from app.exceptions import RedirectException
from app.supabase_client import get_anon_client, get_service_client
from app.templating import templates
from app.email_domains import is_allowed_education_email

router = APIRouter()


@router.get("/signup")
async def signup_page(request: Request):
    return templates.TemplateResponse(request, "signup.html", {})


@router.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    display_name: str = Form(""),
):
    if not is_allowed_education_email(email):
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "가입은 한국 교육청 이메일(예: 인천 @ice.go.kr)로만 가능합니다.",
            },
        )

    client = get_anon_client()
    try:
        result = client.auth.sign_up({"email": email, "password": password})
    except Exception as exc:
        return templates.TemplateResponse(
            request, "signup.html", {"error": f"가입에 실패했습니다: {exc}"}
        )

    if display_name and result.user:
        try:
            get_service_client().table("profiles").update(
                {"display_name": display_name}
            ).eq("id", result.user.id).execute()
        except Exception:
            pass

    if result.session is None:
        # Supabase는 이메일 열거 공격을 막기 위해 이미 가입된 이메일에도 에러 없이
        # 응답한다. identities가 비어있으면 신규 가입이 아니라 기존 계정이라는 뜻.
        if result.user and not result.user.identities:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"message": "이미 가입된 이메일입니다. 로그인해 주세요."},
            )
        # 이메일 확인이 필요한 프로젝트 설정인 경우
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "message": "가입 확인 메일을 발송했습니다. 이메일 인증 후 로그인해 주세요. "
                "인증 후에도 관리자 승인 전까지는 생성 기능을 사용할 수 없습니다.",
            },
        )

    _set_session(request, result)
    return RedirectResponse("/pending", status_code=303)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    client = get_anon_client()
    try:
        result = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "이메일 또는 비밀번호가 올바르지 않습니다."},
        )

    _set_session(request, result)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/pending")
async def pending_page(request: Request, current: CurrentUser = Depends(get_current_user)):
    if current["profile"]["status"] == "approved":
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(request, "pending.html", {"profile": current["profile"]})


@router.post("/account/delete")
async def delete_account(request: Request, current: CurrentUser = Depends(get_current_user)):
    """사용자 본인 요청에 의한 즉시 하드 삭제 (복구 불가). 관리자 계정은 삭제할 수 없다."""
    if current["profile"]["role"] == "admin":
        raise HTTPException(status_code=403, detail="관리자 계정은 삭제할 수 없습니다.")

    service = get_service_client()
    user_id = current["user_id"]
    service.table("generations").delete().eq("user_id", user_id).execute()
    service.table("profiles").delete().eq("id", user_id).execute()
    try:
        service.auth.admin.delete_user(user_id)
    except Exception:
        pass
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _set_session(request: Request, auth_result) -> None:
    request.session["access_token"] = auth_result.session.access_token
    request.session["refresh_token"] = auth_result.session.refresh_token
    request.session["user_id"] = auth_result.user.id
    request.session["email"] = auth_result.user.email
