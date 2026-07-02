from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.deps import CurrentUser, require_approved
from app.pii import SENSITIVE_INFO_NOTICE, contains_rrn
from app.supabase_client import get_service_client, get_user_client
from app.templating import templates
from app.usage import days_until_reset, ledger_status, record_generation

router = APIRouter()

CATEGORIES = [
    "교과학습발달상황",
    "행동특성 및 종합의견",
    "진로활동",
    "자율/동아리/봉사활동",
    "기타",
]

SYSTEM_PROMPT = """당신은 대한민국 초·중·고 교사의 학교생활기록부(생기부) 문구 작성을 돕는 보조 도구입니다.
다음 규칙을 반드시 지켜 작성하세요.
1. 제공된 관찰 자료에 있는 사실만을 근거로 서술하고, 근거 없는 내용을 추가하거나 과장하지 않습니다.
2. 문체는 학교생활기록부 작성 요령에 맞는 개조식이 아닌 서술형 문장으로, 종결어미는 '~함', '~임' 형태의 명사형 종결을 사용합니다.
3. 학생을 주어로 3인칭 관점에서 객관적으로 서술하고, 1인칭 표현은 사용하지 않습니다.
4. 글자수 제한이 주어지면 공백 포함 해당 글자수를 넘지 않도록 작성합니다.
5. 결과는 완성된 문단 형태의 생기부 문구만 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""


def _build_user_prompt(student_label: str, category: str, observation: str, char_limit: int | None) -> str:
    lines = [f"학생: {student_label}"]
    if category:
        lines.append(f"기재 영역: {category}")
    if char_limit:
        lines.append(f"글자수 제한: 공백 포함 {char_limit}자 이내")
    lines.append("관찰 자료:")
    lines.append(observation)
    lines.append("\n위 관찰 자료를 바탕으로 생기부 문구를 작성해 주세요.")
    return "\n".join(lines)


def _is_unlimited(profile: dict) -> bool:
    return profile["role"] == "admin"


def _dashboard_context(current: CurrentUser, error: str | None = None, result: str | None = None) -> dict:
    client = get_user_client(current["access_token"])
    unlimited = _is_unlimited(current["profile"])
    status = ledger_status(get_service_client(), current["profile"]["email"])
    used = status["used"]
    remaining = None if unlimited else max(settings.monthly_limit - used, 0)
    reset_days = days_until_reset(status["period_start"])
    history = (
        client.table("generations")
        .select("*")
        .eq("user_id", current["user_id"])
        .order("created_at", desc=True)
        .limit(20)
        .execute()
        .data
    )
    return {
        "profile": current["profile"],
        "categories": CATEGORIES,
        "used": used,
        "limit": settings.monthly_limit,
        "unlimited": unlimited,
        "remaining": remaining,
        "reset_days": reset_days,
        "history": history,
        "sensitive_info_notice": SENSITIVE_INFO_NOTICE,
        "error": error,
        "result": result,
    }


@router.get("/dashboard")
async def dashboard(request: Request, current: CurrentUser = Depends(require_approved)):
    context = _dashboard_context(current)
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/generate")
async def generate(
    request: Request,
    student_label: str = Form(...),
    category: str = Form(""),
    observation: str = Form(...),
    char_limit: int | None = Form(None),
    current: CurrentUser = Depends(require_approved),
):
    client = get_user_client(current["access_token"])
    service_client = get_service_client()
    status = ledger_status(service_client, current["profile"]["email"])
    used = status["used"]

    if not _is_unlimited(current["profile"]) and used >= settings.monthly_limit:
        context = _dashboard_context(
            current,
            error=f"이번 주기 사용 한도({settings.monthly_limit}건)를 모두 사용했습니다. "
            "다음 리셋일까지 생성 기능을 사용할 수 없습니다.",
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if contains_rrn(student_label, observation):
        context = _dashboard_context(
            current,
            error="입력 내용에 주민등록번호로 의심되는 패턴이 포함되어 있어 요청을 차단했습니다. "
            "민감정보를 제거한 뒤 다시 시도해 주세요.",
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if not settings.anthropic_api_key:
        context = _dashboard_context(
            current,
            error="관리자가 아직 Anthropic API 키를 설정하지 않았습니다. 잠시 후 다시 시도해 주세요.",
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    from anthropic import Anthropic

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    user_prompt = _build_user_prompt(student_label, category, observation, char_limit)

    try:
        response = anthropic_client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        output_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
    except Exception as exc:
        context = _dashboard_context(current, error=f"Claude API 호출 중 오류가 발생했습니다: {exc}")
        return templates.TemplateResponse(request, "dashboard.html", context)

    client.table("generations").insert(
        {
            "user_id": current["user_id"],
            "student_label": student_label,
            "category": category or None,
            "input_text": observation,
            "output_text": output_text,
            "model": settings.anthropic_model,
        }
    ).execute()

    if not _is_unlimited(current["profile"]):
        record_generation(service_client, current["profile"]["email"], used)

    context = _dashboard_context(current, result=output_text)
    return templates.TemplateResponse(request, "dashboard.html", context)
