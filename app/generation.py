import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.charcount import neis_char_count
from app.config import settings
from app.deps import CurrentUser, require_approved
from app.pii import SENSITIVE_INFO_NOTICE, contains_rrn
from app.supabase_client import get_service_client, get_user_client
from app.templating import templates
from app.usage import days_until_reset, ledger_status, record_generation

router = APIRouter()

ACHIEVEMENT_CRITERIA = ["A", "B", "C", "D", "E"]
COMBINED_CHAR_LIMIT = 1000

SYSTEM_PROMPT = """당신은 대한민국 초·중·고 교사의 학교생활기록부(생기부) 문구 작성을 돕는 보조 도구입니다.
다음 규칙을 반드시 지켜 작성하세요.
1. 제공된 관찰 자료에 있는 사실만을 근거로 서술하고, 근거 없는 내용을 추가하거나 과장하지 않습니다.
2. 문체는 학교생활기록부 작성 요령에 맞는 개조식이 아닌 서술형 문장으로, 종결어미는 '~함', '~임' 형태의 명사형 종결을 사용합니다.
3. 학생을 주어로 3인칭 관점에서 객관적으로 서술하고, 1인칭 표현은 사용하지 않습니다.
4. activity1과 activity2 결과를 합친 전체 글자수가 지정된 글자수 제한을 넘지 않도록, 각 활동을 절반 정도의 분량으로 간결하게 작성합니다.
5. 결과는 반드시 activity1, activity2 두 개의 필드를 가진 JSON으로만 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""


def _build_user_prompt(
    student_id: str,
    academic_achievement: str,
    activity1_criterion: str,
    activity1_text: str,
    activity2_criterion: str,
    activity2_text: str,
    char_limit: int,
) -> str:
    lines = [f"학번: {student_id}"]
    if academic_achievement:
        lines.append(f"학업 성취도: {academic_achievement}")
    lines.append(f"[활동1] 성취기준: {activity1_criterion}")
    lines.append(f"[활동1] 관찰 자료: {activity1_text}")
    lines.append(f"[활동2] 성취기준: {activity2_criterion}")
    lines.append(f"[활동2] 관찰 자료: {activity2_text}")
    lines.append(
        f"\n위 관찰 자료를 바탕으로 활동1, 활동2 각각의 생기부 문구를 작성해 주세요. "
        f"activity1과 activity2를 합친 전체 글자수가 공백 포함 {char_limit}자를 넘지 않도록 해주세요."
    )
    return "\n".join(lines)


def _is_unlimited(profile: dict) -> bool:
    return profile["role"] == "admin"


def _dashboard_context(current: CurrentUser, error: str | None = None, result: dict | None = None) -> dict:
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
        "criteria": ACHIEVEMENT_CRITERIA,
        "char_limit": COMBINED_CHAR_LIMIT,
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
    student_id: str = Form(...),
    academic_achievement: str = Form(""),
    activity1_criterion: str = Form(...),
    activity1_text: str = Form(...),
    activity2_criterion: str = Form(...),
    activity2_text: str = Form(...),
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

    if contains_rrn(student_id, academic_achievement, activity1_text, activity2_text):
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
    user_prompt = _build_user_prompt(
        student_id,
        academic_achievement,
        activity1_criterion,
        activity1_text,
        activity2_criterion,
        activity2_text,
        COMBINED_CHAR_LIMIT,
    )

    try:
        response = anthropic_client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "activity1": {"type": "string"},
                            "activity2": {"type": "string"},
                        },
                        "required": ["activity1", "activity2"],
                        "additionalProperties": False,
                    },
                }
            },
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
        activity1_result = data["activity1"]
        activity2_result = data["activity2"]
    except Exception as exc:
        context = _dashboard_context(current, error=f"Claude API 호출 중 오류가 발생했습니다: {exc}")
        return templates.TemplateResponse(request, "dashboard.html", context)

    activity1_count = neis_char_count(activity1_result)
    activity2_count = neis_char_count(activity2_result)
    total_count = activity1_count + activity2_count

    client.table("generations").insert(
        {
            "user_id": current["user_id"],
            "student_label": student_id,
            "category": f"성취기준 {activity1_criterion}/{activity2_criterion}",
            "input_text": user_prompt,
            "output_text": f"[활동1]\n{activity1_result}\n\n[활동2]\n{activity2_result}",
            "model": settings.anthropic_model,
        }
    ).execute()

    if not _is_unlimited(current["profile"]):
        record_generation(service_client, current["profile"]["email"], used)

    result = {
        "activity1": activity1_result,
        "activity2": activity2_result,
        "activity1_count": activity1_count,
        "activity2_count": activity2_count,
        "total_count": total_count,
        "over_limit": total_count > COMBINED_CHAR_LIMIT,
    }
    context = _dashboard_context(current, result=result)
    return templates.TemplateResponse(request, "dashboard.html", context)
