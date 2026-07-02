import json

from fastapi import APIRouter, Depends, Request

from app.charcount import neis_char_count
from app.config import settings
from app.deps import CurrentUser, require_approved
from app.pii import SENSITIVE_INFO_NOTICE, contains_rrn
from app.subject_criteria import criterion_label, get_criteria, get_subjects
from app.supabase_client import get_service_client, get_user_client
from app.templating import templates
from app.usage import days_until_reset, ledger_status, record_generation

router = APIRouter()

ACADEMIC_ACHIEVEMENT_LEVELS = ["A", "B", "C", "D", "E"]

DEFAULT_MIN_CHAR_LIMIT = 600
DEFAULT_MAX_CHAR_LIMIT = 700
# 나이스 글자수 계산 기준 상한. 교사가 이보다 큰 값을 지정할 수 없다
# (실제 나이스 입력 필드 제한, 한글 기준 약 3000바이트에 해당).
HARD_MAX_CHAR_LIMIT = 1000
MAX_ACTIVITIES = 10

# 과목명을 프롬프트에 직접 넣으면 요청마다 시스템 프롬프트가 달라져 프롬프트
# 캐싱이 깨지므로, 과목은 시스템 프롬프트가 아닌 사용자 프롬프트 쪽에 담는다.
SYSTEM_PROMPT = """당신은 대한민국 고등학교 교사입니다. 담당 교과목 학생의 교과 세부능력 및 특기사항(세특)을 작성하는 것을 돕는 보조 도구입니다.
다음 조건을 반드시 지켜 작성하세요.
1. 문장은 종결형 어미(~함, ~임, ~음 등 명사형 종결)로 끝맺습니다. 개조식이 아닌 서술형 문장으로 씁니다.
2. 교과 성취 수준을 바탕으로 교과 역량을 강조하고, 학습에 의한 변화와 성장 가능성을 중심으로 기재합니다.
3. 제공된 활동 관찰 자료를 근거로 학생의 수행 특기사항을 구체적으로 서술합니다. 근거 없는 내용을 추가하거나 과장하지 않습니다.
4. 긍정적인 내용만 서술합니다.
5. 서술 순서는 반드시 "교과 성취 수준 → 수행 특기사항 → 교과 역량 → 수업 태도" 순서를 따릅니다.
6. 교사의 관점에서, 학생을 주어로 한 3인칭 시점으로 서술합니다 (1인칭 표현 금지).
7. 입력된 활동이 여러 개이더라도 활동별로 나누어 쓰지 않고, 이를 모두 종합해 하나의 통일된 문단으로 작성합니다.
8. 전체 글자수(나이스 글자수 계산 기준, 공백 포함)는 사용자가 지정한 최소/최대 글자수 범위를 지켜 작성합니다.
9. 결과는 반드시 result라는 문자열 하나만 가진 JSON으로 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""


def _build_user_prompt(
    student_id: str,
    subject: str,
    academic_achievement: str,
    activities: list[tuple[str, str]],
    min_char_limit: int,
    max_char_limit: int,
) -> str:
    lines = [f"학번: {student_id}", f"담당 교과목: {subject}"]
    if academic_achievement:
        lines.append(f"교과 성취 수준: {academic_achievement}")
    for index, (criterion, text) in enumerate(activities, start=1):
        lines.append(f"[활동{index}] 성취기준: {criterion_label(subject, criterion)}")
        lines.append(f"[활동{index}] 관찰 자료: {text}")
    lines.append(
        f"\n위 교과 성취 수준과 활동 {len(activities)}개의 관찰 자료를 모두 반영해, "
        f"하나의 세부능력 및 특기사항 문단을 작성해 주세요. "
        f"목표 글자수: 공백 포함 {min_char_limit}자 이상 {max_char_limit}자 이하 (나이스 글자수 계산 기준)."
    )
    return "\n".join(lines)


def _is_unlimited(profile: dict) -> bool:
    return profile["role"] == "admin"


def _clamp_char_limits(min_raw: str, max_raw: str) -> tuple[int, int] | None:
    try:
        min_limit = int(min_raw)
        max_limit = int(max_raw)
    except (TypeError, ValueError):
        return None
    if min_limit < 1 or max_limit < 1:
        return None
    max_limit = min(max_limit, HARD_MAX_CHAR_LIMIT)
    if min_limit > max_limit:
        return None
    return min_limit, max_limit


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
    subjects = get_subjects()
    subject_criteria_json = json.dumps(
        {subject: get_criteria(subject) for subject in subjects}, ensure_ascii=False
    )
    return {
        "profile": current["profile"],
        "subjects": subjects,
        "subject_criteria_json": subject_criteria_json,
        "academic_levels": ACADEMIC_ACHIEVEMENT_LEVELS,
        "default_min_char_limit": DEFAULT_MIN_CHAR_LIMIT,
        "default_max_char_limit": DEFAULT_MAX_CHAR_LIMIT,
        "hard_max_char_limit": HARD_MAX_CHAR_LIMIT,
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
async def generate(request: Request, current: CurrentUser = Depends(require_approved)):
    form = await request.form()
    student_id = str(form.get("student_id", "")).strip()
    subject = str(form.get("subject", "")).strip()
    academic_achievement = str(form.get("academic_achievement", "")).strip()
    criteria_values = form.getlist("activity_criterion")
    text_values = form.getlist("activity_text")
    activities = [
        (str(criterion), str(text).strip())
        for criterion, text in zip(criteria_values, text_values)
        if str(text).strip()
    ][:MAX_ACTIVITIES]

    char_limits = _clamp_char_limits(
        form.get("min_char_limit", str(DEFAULT_MIN_CHAR_LIMIT)),
        form.get("max_char_limit", str(DEFAULT_MAX_CHAR_LIMIT)),
    )

    if not student_id or subject not in get_subjects() or not activities:
        context = _dashboard_context(
            current, error="학번, 과목, 최소 1개 이상의 활동 내용을 입력해 주세요."
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if char_limits is None:
        context = _dashboard_context(
            current,
            error=f"글자수 설정이 올바르지 않습니다. 최대 글자수는 {HARD_MAX_CHAR_LIMIT}자를 넘을 수 없고, "
            "최소 글자수는 최대 글자수보다 작아야 합니다.",
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    min_char_limit, max_char_limit = char_limits

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

    if contains_rrn(student_id, academic_achievement, *[text for _, text in activities]):
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
        student_id, subject, academic_achievement, activities, min_char_limit, max_char_limit
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
                            "result": {"type": "string"},
                        },
                        "required": ["result"],
                        "additionalProperties": False,
                    },
                }
            },
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
        result_text = str(data["result"])
    except Exception as exc:
        context = _dashboard_context(current, error=f"Claude API 호출 중 오류가 발생했습니다: {exc}")
        return templates.TemplateResponse(request, "dashboard.html", context)

    char_count = neis_char_count(result_text)

    client.table("generations").insert(
        {
            "user_id": current["user_id"],
            "student_label": student_id,
            "category": f"{subject} · 성취기준 " + "/".join(criterion for criterion, _ in activities),
            "input_text": user_prompt,
            "output_text": result_text,
            "model": settings.anthropic_model,
        }
    ).execute()

    if not _is_unlimited(current["profile"]):
        record_generation(service_client, current["profile"]["email"], used)

    result = {
        "text": result_text,
        "count": char_count,
        "min_char_limit": min_char_limit,
        "max_char_limit": max_char_limit,
        "out_of_range": char_count < min_char_limit or char_count > max_char_limit,
    }
    context = _dashboard_context(current, result=result)
    return templates.TemplateResponse(request, "dashboard.html", context)
