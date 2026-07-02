import json

from fastapi import APIRouter, Depends, Request

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
MAX_ACTIVITIES = 10

SYSTEM_PROMPT = """당신은 대한민국 초·중·고 교사의 학교생활기록부(생기부) 문구 작성을 돕는 보조 도구입니다.
다음 규칙을 반드시 지켜 작성하세요.
1. 제공된 관찰 자료에 있는 사실만을 근거로 서술하고, 근거 없는 내용을 추가하거나 과장하지 않습니다.
2. 문체는 학교생활기록부 작성 요령에 맞는 개조식이 아닌 서술형 문장으로, 종결어미는 '~함', '~임' 형태의 명사형 종결을 사용합니다.
3. 학생을 주어로 3인칭 관점에서 객관적으로 서술하고, 1인칭 표현은 사용하지 않습니다.
4. 활동은 입력받은 순서 그대로, 입력된 개수와 동일한 개수만큼 결과를 작성합니다. 전체 활동 결과를 합친 글자수가 지정된 글자수 제한을 넘지 않도록, 활동 개수에 맞게 분량을 균등히 나눠 간결하게 작성합니다.
5. 결과는 반드시 activities라는 문자열 배열 하나만 가진 JSON으로 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""


def _build_user_prompt(
    student_id: str,
    academic_achievement: str,
    activities: list[tuple[str, str]],
    char_limit: int,
) -> str:
    lines = [f"학번: {student_id}"]
    if academic_achievement:
        lines.append(f"학업 성취도: {academic_achievement}")
    for index, (criterion, text) in enumerate(activities, start=1):
        lines.append(f"[활동{index}] 성취기준: {criterion}")
        lines.append(f"[활동{index}] 관찰 자료: {text}")
    lines.append(
        f"\n위 관찰 자료를 바탕으로 활동 {len(activities)}개 각각의 생기부 문구를 작성해 주세요. "
        f"전체 활동 결과를 합친 글자수가 공백 포함 {char_limit}자를 넘지 않도록 해주세요."
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
async def generate(request: Request, current: CurrentUser = Depends(require_approved)):
    form = await request.form()
    student_id = str(form.get("student_id", "")).strip()
    academic_achievement = str(form.get("academic_achievement", "")).strip()
    criteria_values = form.getlist("activity_criterion")
    text_values = form.getlist("activity_text")
    activities = [
        (str(criterion), str(text).strip())
        for criterion, text in zip(criteria_values, text_values)
        if str(text).strip()
    ][:MAX_ACTIVITIES]

    if not student_id or not activities:
        context = _dashboard_context(
            current, error="학번과 최소 1개 이상의 활동 내용을 입력해 주세요."
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

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
    user_prompt = _build_user_prompt(student_id, academic_achievement, activities, COMBINED_CHAR_LIMIT)

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
                            "activities": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["activities"],
                        "additionalProperties": False,
                    },
                }
            },
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = next(block.text for block in response.content if block.type == "text")
        data = json.loads(text)
        activity_results = [str(item) for item in data["activities"]]
    except Exception as exc:
        context = _dashboard_context(current, error=f"Claude API 호출 중 오류가 발생했습니다: {exc}")
        return templates.TemplateResponse(request, "dashboard.html", context)

    # 모델이 입력 개수와 다르게 반환하는 경우를 대비해 개수를 맞춰준다.
    while len(activity_results) < len(activities):
        activity_results.append("")
    activity_results = activity_results[: len(activities)]

    activity_counts = [neis_char_count(text) for text in activity_results]
    total_count = sum(activity_counts)

    client.table("generations").insert(
        {
            "user_id": current["user_id"],
            "student_label": student_id,
            "category": "성취기준 " + "/".join(criterion for criterion, _ in activities),
            "input_text": user_prompt,
            "output_text": "\n\n".join(
                f"[활동{i}]\n{text}" for i, text in enumerate(activity_results, start=1)
            ),
            "model": settings.anthropic_model,
        }
    ).execute()

    if not _is_unlimited(current["profile"]):
        record_generation(service_client, current["profile"]["email"], used)

    result = {
        "activities": [
            {"index": i, "text": text, "count": count}
            for i, (text, count) in enumerate(zip(activity_results, activity_counts), start=1)
        ],
        "total_count": total_count,
        "over_limit": total_count > COMBINED_CHAR_LIMIT,
    }
    context = _dashboard_context(current, result=result)
    return templates.TemplateResponse(request, "dashboard.html", context)
