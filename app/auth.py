from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.deps import CurrentUser, RedirectException, get_current_user, templates
from app.supabase_client import get_anon_client, get_service_client
from app.email_domains import EDUCATION_OFFICE_DOMAINS, GENERIC_GOV_DOMAINS, is_allowed_education_email
from app.subject_criteria import get_subjects

router = APIRouter()

# 로그인/회원가입 화면에서 이메일 도메인을 골라 쓸 수 있게 보여주는 목록. 실제
# 가입자 이메일 목록은 비인증 상태에서 노출하면 피싱 등에 악용될 수 있어 절대
# 보여주지 않고, 이미 공개 정보인 교육청 도메인 이름만 후보로 제공한다.
# 실사용자 대부분이 인천(ice.go.kr) 소속이라 그 도메인을 맨 위에 고정하고,
# 그다음으로 자주 쓰이는 공직자통합메일(korea.kr)을 바로 이어 붙인다.
_PINNED_EMAIL_DOMAIN = "ice.go.kr"
EMAIL_DOMAIN_CHOICES = (
    [_PINNED_EMAIL_DOMAIN]
    + sorted(GENERIC_GOV_DOMAINS)
    + sorted(EDUCATION_OFFICE_DOMAINS - {_PINNED_EMAIL_DOMAIN})
)


@router.get("/signup")
async def signup_page(request: Request):
    return templates.TemplateResponse(
        request, "signup.html", {"subjects": get_subjects(), "domains": EMAIL_DOMAIN_CHOICES}
    )


@router.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    school_name: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(""),
    display_name: str = Form(""),
    subjects: list[str] = Form([]),
):
    valid_subjects = [s for s in subjects if s in get_subjects()]
    school_name = school_name.strip()

    if not is_allowed_education_email(email):
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "가입은 한국 교육청 이메일(예: 인천 @ice.go.kr) 또는 공직자통합메일(@korea.kr)로만 가능합니다.",
                "subjects": get_subjects(),
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    if not school_name:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "소속 학교/기관명을 입력해 주세요.",
                "subjects": get_subjects(),
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "비밀번호가 일치하지 않습니다.",
                "subjects": get_subjects(),
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    if not valid_subjects:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "담당 과목을 1개 이상 선택해 주세요.",
                "subjects": get_subjects(),
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    client = get_anon_client()
    try:
        result = await run_in_threadpool(client.auth.sign_up, {"email": email, "password": password})
    except Exception as exc:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": f"가입에 실패했습니다: {exc}",
                "subjects": get_subjects(),
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    profile_update = {"school_name": school_name}
    if display_name:
        profile_update["display_name"] = display_name
    if valid_subjects:
        profile_update["subjects"] = valid_subjects
    if profile_update and result.user:
        try:
            await run_in_threadpool(
                lambda: get_service_client()
                .table("profiles")
                .update(profile_update)
                .eq("id", result.user.id)
                .execute()
            )
        except Exception:
            pass

    if result.session is None:
        # Supabase는 이메일 열거 공격을 막기 위해 이미 가입된 이메일에도 에러 없이
        # 응답한다. identities가 비어있으면 신규 가입이 아니라 기존 계정이라는 뜻.
        if result.user and not result.user.identities:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"message": "이미 가입된 이메일입니다. 로그인해 주세요.", "domains": EMAIL_DOMAIN_CHOICES},
            )
        # 이메일 확인이 필요한 프로젝트 설정인 경우
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "message": "가입 확인 메일을 발송했습니다. 이메일 인증 후 로그인해 주세요. "
                "인증 후에도 관리자 승인 전까지는 생성 기능을 사용할 수 없습니다.",
                "domains": EMAIL_DOMAIN_CHOICES,
            },
        )

    _set_session(request, result)
    return RedirectResponse("/pending", status_code=303)


@router.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"domains": EMAIL_DOMAIN_CHOICES})


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    client = get_anon_client()
    try:
        result = await run_in_threadpool(
            client.auth.sign_in_with_password, {"email": email, "password": password}
        )
    except Exception:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "이메일 또는 비밀번호가 올바르지 않습니다.", "domains": EMAIL_DOMAIN_CHOICES},
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


@router.get("/account")
async def account_page(request: Request, current: CurrentUser = Depends(get_current_user)):
    if current["profile"]["role"] == "admin":
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse(
        request,
        "account.html",
        {"profile": current["profile"], "active_nav": "account", "subjects": get_subjects()},
    )


@router.post("/account/subjects")
async def update_subjects(
    request: Request,
    current: CurrentUser = Depends(get_current_user),
    subjects: list[str] = Form([]),
):
    if current["profile"]["role"] == "admin":
        return RedirectResponse("/dashboard", status_code=303)

    valid_subjects = [s for s in subjects if s in get_subjects()]
    await run_in_threadpool(
        lambda: get_service_client()
        .table("profiles")
        .update({"subjects": valid_subjects})
        .eq("id", current["user_id"])
        .execute()
    )
    updated_profile = dict(current["profile"])
    updated_profile["subjects"] = valid_subjects
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "profile": updated_profile,
            "active_nav": "account",
            "subjects": get_subjects(),
            "notice": "담당 과목이 저장되었습니다.",
        },
    )


@router.post("/account/delete")
async def delete_account(request: Request, current: CurrentUser = Depends(get_current_user)):
    """사용자 본인 요청에 의한 즉시 하드 삭제 (복구 불가). 관리자 계정은 삭제할 수 없다."""
    if current["profile"]["role"] == "admin":
        raise HTTPException(status_code=403, detail="관리자 계정은 삭제할 수 없습니다.")

    service = get_service_client()
    user_id = current["user_id"]

    def _delete_account_data():
        service.table("generations").delete().eq("user_id", user_id).execute()
        service.table("profiles").delete().eq("id", user_id).execute()
        try:
            service.auth.admin.delete_user(user_id)
        except Exception:
            pass

    await run_in_threadpool(_delete_account_data)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


def _set_session(request: Request, auth_result) -> None:
    request.session["access_token"] = auth_result.session.access_token
    request.session["refresh_token"] = auth_result.session.refresh_token
    request.session["user_id"] = auth_result.user.id
    request.session["email"] = auth_result.user.email
