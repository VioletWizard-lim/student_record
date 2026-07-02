import json
import re

from fastapi import APIRouter, Depends, Request
from starlette.datastructures import FormData

from app.charcount import neis_byte_count
from app.config import settings
from app.deps import CurrentUser, require_approved, templates
from app.pii import SENSITIVE_INFO_NOTICE, contains_rrn
from app.subject_criteria import criterion_label, get_criteria, get_subjects
from app.supabase_client import get_service_client, get_user_client
from app.usage import days_until_reset, ledger_status, record_generation

router = APIRouter()

ACADEMIC_ACHIEVEMENT_LEVELS = ["A", "B", "C", "D", "E"]

DEFAULT_MIN_CHAR_LIMIT = 600
DEFAULT_MAX_CHAR_LIMIT = 700
# 나이스 바이트 계산 기준 상한. 교사가 이보다 큰 값을 지정할 수 없다
# (실제 나이스 입력 필드 제한).
HARD_MAX_CHAR_LIMIT = 1000
MAX_ACTIVITIES = 10
# 한 번에 처리할 수 있는 최대 학생 수 (요청 하나에 순차적으로 Claude API를
# 여러 번 호출하므로, 처리 시간/비용을 고려해 상한을 둔다).
MAX_STUDENTS_PER_BATCH = 20
# 이력 페이지에서 검색/정렬 대상으로 불러오는 최대 건수.
HISTORY_FETCH_LIMIT = 500

# 과목명을 프롬프트에 직접 넣으면 요청마다 시스템 프롬프트가 달라져 프롬프트
# 캐싱이 깨지므로, 과목은 시스템 프롬프트가 아닌 사용자 프롬프트 쪽에 담는다.
SYSTEM_PROMPT = """당신은 대한민국 고등학교 교사입니다. 담당 교과목 학생의 교과 세부능력 및 특기사항(세특)을 작성하는 것을 돕는 보조 도구입니다.
다음 조건을 반드시 지켜 작성하세요.
1. 문장은 종결형 어미(~함, ~임, ~음 등 명사형 종결)로 끝맺습니다. 개조식이 아닌 서술형 문장으로 씁니다.
2. 교과 성취 수준은 문장의 깊이·어조·강조할 역량 수준을 정하는 참고 자료로만 사용합니다. "A 수준", "성취도가 우수함", "높은 성취 수준을 보임"처럼 성취 수준 자체를 문장에 직접 언급하지 않고, 학습 내용과 성장 가능성 서술에 자연스럽게 녹여냅니다.
3. 제공된 활동 관찰 자료를 근거로 학생의 수행 특기사항을 구체적으로 서술합니다. 근거 없는 내용을 추가하거나 과장하지 않습니다.
4. 긍정적인 내용만 서술합니다.
5. 서술 순서는 반드시 "교과 성취 수준에 대한 서술(직접 언급 없이) → 수행 특기사항 → 교과 역량 → 수업 태도" 순서를 따릅니다.
6. 교사의 관점에서, 학생을 주어로 한 3인칭 시점으로 서술합니다 (1인칭 표현 금지).
7. 입력된 활동이 여러 개이더라도 활동별로 나누어 쓰지 않고, 이를 모두 종합해 하나의 통일된 문단으로 작성합니다.
8. 전체 바이트 수(나이스 바이트 계산 기준, 공백 포함)는 사용자가 지정한 최소/최대 바이트 범위를 지켜 작성합니다.
9. 결과는 반드시 result라는 문자열 하나만 가진 JSON으로 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""

STUDENT_ID_KEY_RE = re.compile(r"^student_id__(\d+)$")


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
        f"목표 바이트 수: 공백 포함 {min_char_limit}바이트 이상 {max_char_limit}바이트 이하 (나이스 바이트 계산 기준)."
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


def _empty_student(index: int = 0) -> dict:
    return {
        "index": index,
        "student_id": "",
        "subject": "",
        "academic_achievement": "",
        "activities": [{"criterion": "", "text": ""}, {"criterion": "", "text": ""}],
    }


def _parse_students_raw(form: FormData) -> list[dict]:
    """폼에서 student_id__0, subject__0, activity_criterion__0 ... 형태의
    인덱스가 붙은 필드들을 학생별로 묶어낸다. 인덱스는 연속적이지 않아도 된다
    (중간 학생을 화면에서 삭제해도 나머지 인덱스는 그대로 유지되므로).

    유효성 검사는 하지 않고 입력된 값을 그대로 담는다. 임시저장 및 오류 발생 시
    입력하던 내용을 화면에 그대로 되돌려 보여주는 데 사용한다."""
    indices = sorted(
        {
            int(match.group(1))
            for key in form.keys()
            for match in [STUDENT_ID_KEY_RE.match(key)]
            if match
        }
    )
    students = []
    for index in indices[:MAX_STUDENTS_PER_BATCH]:
        criteria_values = form.getlist(f"activity_criterion__{index}")
        text_values = form.getlist(f"activity_text__{index}")
        activities = [
            {"criterion": str(criterion), "text": str(text)}
            for criterion, text in zip(criteria_values, text_values)
        ][:MAX_ACTIVITIES]
        students.append(
            {
                "index": index,
                "student_id": str(form.get(f"student_id__{index}", "")).strip(),
                "subject": str(form.get(f"subject__{index}", "")).strip(),
                "academic_achievement": str(form.get(f"academic_achievement__{index}", "")).strip(),
                "activities": activities or [{"criterion": "", "text": ""}],
            }
        )
    return students or [_empty_student()]


def _parse_students(form: FormData) -> tuple[list[dict], list[dict]]:
    """생성 요청용: 학번/과목/활동이 모두 채워진 학생만 골라, 활동을
    (성취기준, 텍스트) 튜플 리스트로 변환한다. 조건을 만족하지 못해 제외된
    학생은 이유와 함께 두 번째 값(skipped)으로 따로 반환한다."""
    students = []
    skipped = []
    for position, raw in enumerate(_parse_students_raw(form), start=1):
        label = raw["student_id"] or f"{position}번째 학생"
        if not raw["student_id"]:
            skipped.append({"label": label, "reason": "학번이 입력되지 않았습니다."})
            continue
        if raw["subject"] not in get_subjects():
            skipped.append({"label": label, "reason": "과목이 선택되지 않았습니다."})
            continue
        activities = [
            (activity["criterion"], activity["text"].strip())
            for activity in raw["activities"]
            if activity["text"].strip()
        ][:MAX_ACTIVITIES]
        if not activities:
            skipped.append({"label": label, "reason": "활동 관찰 자료가 입력되지 않았습니다."})
            continue
        criteria = [criterion for criterion, _ in activities]
        if len(criteria) != len(set(criteria)):
            skipped.append(
                {
                    "label": label,
                    "reason": "같은 성취기준을 두 개 이상의 활동에 중복 선택했습니다. "
                    "활동마다 서로 다른 성취기준을 선택해 주세요.",
                }
            )
            continue
        students.append(
            {
                "student_id": raw["student_id"],
                "subject": raw["subject"],
                "academic_achievement": raw["academic_achievement"],
                "activities": activities,
            }
        )
    return students, skipped


def _existing_student_labels(current: CurrentUser) -> set[str]:
    """이 사용자가 이전에 생성한 적 있는 학번 목록을 조회한다. 재생성 시
    화면(서버/클라이언트 양쪽)에서 "이미 이력이 있는 학번" 경고에 쓰인다."""
    client = get_user_client(current["access_token"])
    rows = (
        client.table("generations")
        .select("student_label")
        .eq("user_id", current["user_id"])
        .execute()
        .data
    )
    return {row["student_label"] for row in rows}


def _load_draft(current: CurrentUser) -> dict | None:
    """사용자가 임시저장해 둔 폼 데이터를 불러온다. 저장된 초안이 없거나
    drafts 테이블이 아직 반영되지 않은 환경이면 None을 반환한다."""
    client = get_user_client(current["access_token"])
    try:
        response = (
            client.table("drafts").select("data").eq("user_id", current["user_id"]).single().execute()
        )
    except Exception:
        return None
    return response.data.get("data") if response.data else None


def _dashboard_context(
    current: CurrentUser,
    error: str | None = None,
    notice: str | None = None,
    result: list[dict] | None = None,
    students: list[dict] | None = None,
    min_char_limit: int | str | None = None,
    max_char_limit: int | str | None = None,
) -> dict:
    unlimited = _is_unlimited(current["profile"])
    status = ledger_status(get_service_client(), current["profile"]["email"])
    used = status["used"]
    limit = status["monthly_limit"]
    remaining = None if unlimited else max(limit - used, 0)
    reset_days = days_until_reset(status["period_start"])
    subjects = get_subjects()
    subject_criteria_json = json.dumps(
        {subject: get_criteria(subject) for subject in subjects}, ensure_ascii=False
    )
    existing_student_labels_json = json.dumps(
        sorted(_existing_student_labels(current)), ensure_ascii=False
    )

    draft = None
    if students is None or min_char_limit is None or max_char_limit is None:
        draft = _load_draft(current) or {}

    if students is None:
        students = draft.get("students") or [_empty_student()]
    if min_char_limit is None:
        min_char_limit = draft.get("min_char_limit") or DEFAULT_MIN_CHAR_LIMIT
    if max_char_limit is None:
        max_char_limit = draft.get("max_char_limit") or DEFAULT_MAX_CHAR_LIMIT

    next_student_index = max((s["index"] for s in students), default=-1) + 1

    return {
        "profile": current["profile"],
        "subjects": subjects,
        "subject_criteria_json": subject_criteria_json,
        "existing_student_labels_json": existing_student_labels_json,
        "academic_levels": ACADEMIC_ACHIEVEMENT_LEVELS,
        "students": students,
        "next_student_index": next_student_index,
        "min_char_limit": min_char_limit,
        "max_char_limit": max_char_limit,
        "hard_max_char_limit": HARD_MAX_CHAR_LIMIT,
        "max_students_per_batch": MAX_STUDENTS_PER_BATCH,
        "used": used,
        "limit": limit,
        "unlimited": unlimited,
        "remaining": remaining,
        "reset_days": reset_days,
        "sensitive_info_notice": SENSITIVE_INFO_NOTICE,
        "error": error,
        "notice": notice,
        "result": result,
        "active_nav": "dashboard",
    }


@router.get("/dashboard")
async def dashboard(request: Request, current: CurrentUser = Depends(require_approved)):
    context = _dashboard_context(current)
    return templates.TemplateResponse(request, "dashboard.html", context)


HISTORY_SORT_FIELDS = {"created_at", "student_label", "category"}


def _filter_history(history: list[dict], query: str) -> list[dict]:
    """학번/영역/결과 내용에 검색어가 포함된 이력만 남긴다 (대소문자 무시)."""
    query = query.strip()
    if not query:
        return history
    needle = query.lower()
    return [
        row
        for row in history
        if needle in (row.get("student_label") or "").lower()
        or needle in (row.get("category") or "").lower()
        or needle in (row.get("output_text") or "").lower()
    ]


def _sort_history(history: list[dict], sort: str, order: str) -> list[dict]:
    if sort not in HISTORY_SORT_FIELDS:
        sort = "created_at"
    if order not in ("asc", "desc"):
        order = "desc"
    return sorted(history, key=lambda row: row.get(sort) or "", reverse=(order == "desc"))


@router.get("/history")
async def history_page(
    request: Request,
    q: str = "",
    sort: str = "created_at",
    order: str = "desc",
    current: CurrentUser = Depends(require_approved),
):
    if sort not in HISTORY_SORT_FIELDS:
        sort = "created_at"
    if order not in ("asc", "desc"):
        order = "desc"

    client = get_user_client(current["access_token"])
    history = (
        client.table("generations")
        .select("*")
        .eq("user_id", current["user_id"])
        .order("created_at", desc=True)
        .limit(HISTORY_FETCH_LIMIT)
        .execute()
        .data
    )

    query = q.strip()
    history = _filter_history(history, query)
    history = _sort_history(history, sort, order)

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "profile": current["profile"],
            "history": history,
            "active_nav": "history",
            "q": query,
            "sort": sort,
            "order": order,
        },
    )


@router.post("/draft/save")
async def save_draft(request: Request, current: CurrentUser = Depends(require_approved)):
    form = await request.form()
    raw_students = _parse_students_raw(form)
    min_char_raw = form.get("min_char_limit", str(DEFAULT_MIN_CHAR_LIMIT))
    max_char_raw = form.get("max_char_limit", str(DEFAULT_MAX_CHAR_LIMIT))

    draft_data = {
        "students": raw_students,
        "min_char_limit": min_char_raw,
        "max_char_limit": max_char_raw,
    }
    client = get_user_client(current["access_token"])
    client.table("drafts").upsert({"user_id": current["user_id"], "data": draft_data}).execute()

    context = _dashboard_context(
        current,
        notice="임시저장되었습니다.",
        students=raw_students,
        min_char_limit=min_char_raw,
        max_char_limit=max_char_raw,
    )
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/generate")
async def generate(request: Request, current: CurrentUser = Depends(require_approved)):
    form = await request.form()
    raw_students = _parse_students_raw(form)
    students, skipped = _parse_students(form)

    min_char_raw = form.get("min_char_limit", str(DEFAULT_MIN_CHAR_LIMIT))
    max_char_raw = form.get("max_char_limit", str(DEFAULT_MAX_CHAR_LIMIT))
    char_limits = _clamp_char_limits(min_char_raw, max_char_raw)

    if not students:
        if skipped:
            reasons = "; ".join(f"{item['label']}: {item['reason']}" for item in skipped)
            error_message = f"입력한 학생 정보가 올바르지 않아 생성할 수 없습니다. ({reasons})"
        else:
            error_message = "학생을 최소 1명 이상, 각 학생마다 학번/과목/활동 내용을 입력해 주세요."
        context = _dashboard_context(
            current,
            error=error_message,
            students=raw_students,
            min_char_limit=min_char_raw,
            max_char_limit=max_char_raw,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if char_limits is None:
        context = _dashboard_context(
            current,
            error=f"바이트 설정이 올바르지 않습니다. 최대 바이트는 {HARD_MAX_CHAR_LIMIT}바이트를 넘을 수 없고, "
            "최소 바이트는 최대 바이트보다 작아야 합니다.",
            students=raw_students,
            min_char_limit=min_char_raw,
            max_char_limit=max_char_raw,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    min_char_limit, max_char_limit = char_limits

    client = get_user_client(current["access_token"])
    service_client = get_service_client()
    status = ledger_status(service_client, current["profile"]["email"])
    used = status["used"]
    limit = status["monthly_limit"]
    unlimited = _is_unlimited(current["profile"])

    if not unlimited and used + len(students) > limit:
        context = _dashboard_context(
            current,
            error=f"이번 요청(학생 {len(students)}명)을 처리하면 사용 한도({limit}건)를 "
            f"초과합니다. 남은 한도는 {max(limit - used, 0)}건입니다. "
            "학생 수를 줄이거나 다음 리셋일까지 기다려 주세요.",
            students=raw_students,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    pii_check_fields = []
    for student in students:
        pii_check_fields.append(student["student_id"])
        pii_check_fields.append(student["academic_achievement"])
        pii_check_fields.extend(text for _, text in student["activities"])
    if contains_rrn(*pii_check_fields):
        context = _dashboard_context(
            current,
            error="입력 내용에 주민등록번호로 의심되는 패턴이 포함되어 있어 요청을 차단했습니다. "
            "민감정보를 제거한 뒤 다시 시도해 주세요.",
            students=raw_students,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if not settings.anthropic_api_key:
        context = _dashboard_context(
            current,
            error="관리자가 아직 Anthropic API 키를 설정하지 않았습니다. 잠시 후 다시 시도해 주세요.",
            students=raw_students,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    from anthropic import Anthropic

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    results = []

    # 이미 생성 이력이 있는 학번을 다시 생성하면 기존 이력을 덮어쓰지 않고
    # 새 이력으로 추가한다. 대신 결과 화면에서 경고를 표시할 수 있도록,
    # 배치 처리 전 시점의 학번 목록을 미리 조회해 둔다(같은 배치 내 중복 포함).
    existing_labels = _existing_student_labels(current)

    for student in students:
        duplicate_student = student["student_id"] in existing_labels

        user_prompt = _build_user_prompt(
            student["student_id"],
            student["subject"],
            student["academic_achievement"],
            student["activities"],
            min_char_limit,
            max_char_limit,
        )

        try:
            response = anthropic_client.messages.create(
                model=settings.anthropic_model,
                # thinking을 꺼서 사고 과정에 토큰을 쓰지 않으므로, 결과 텍스트
                # (최대 HARD_MAX_CHAR_LIMIT 바이트)만 감당하면 되는 수준으로
                # max_tokens를 낮춘다.
                max_tokens=1000,
                thinking={"type": "disabled"},
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
            text_block = next((block for block in response.content if block.type == "text"), None)
            if text_block is None:
                raise RuntimeError(
                    f"Claude 응답에서 결과 텍스트를 찾지 못했습니다 (stop_reason={response.stop_reason})."
                )
            data = json.loads(text_block.text)
            result_text = str(data["result"])
        except Exception as exc:
            # 일부 예외(예: 빈 StopIteration)는 str()이 빈 문자열이라 메시지가
            # 안 보일 수 있으므로, 비어 있으면 repr()로 대체해 항상 정보를 남긴다.
            error_detail = str(exc) or repr(exc)
            results.append(
                {
                    "student_id": student["student_id"],
                    "error": f"생성 중 오류가 발생했습니다: {error_detail}",
                }
            )
            continue

        char_count = neis_byte_count(result_text)

        # 기존 이력을 덮어쓰지 않고 새 행으로 추가한다.
        client.table("generations").insert(
            {
                "user_id": current["user_id"],
                "student_label": student["student_id"],
                "category": f"{student['subject']} · 성취기준 "
                + "/".join(criterion for criterion, _ in student["activities"]),
                "input_text": user_prompt,
                "output_text": result_text,
                "model": settings.anthropic_model,
            }
        ).execute()
        existing_labels.add(student["student_id"])

        if not unlimited:
            record_generation(service_client, current["profile"]["email"], used)
            used += 1

        results.append(
            {
                "student_id": student["student_id"],
                "text": result_text,
                "count": char_count,
                "min_char_limit": min_char_limit,
                "max_char_limit": max_char_limit,
                "duplicate_student": duplicate_student,
            }
        )

    skip_error = None
    if skipped:
        reasons = "; ".join(f"{item['label']}: {item['reason']}" for item in skipped)
        skip_error = f"다음 학생은 정보가 올바르지 않아 생성에서 제외되었습니다. ({reasons})"

    context = _dashboard_context(
        current,
        error=skip_error,
        result=results,
        students=raw_students,
        min_char_limit=min_char_limit,
        max_char_limit=max_char_limit,
    )
    return templates.TemplateResponse(request, "dashboard.html", context)
