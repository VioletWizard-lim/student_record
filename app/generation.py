import json
import re

from fastapi import APIRouter, Depends, Form, Request
from starlette.concurrency import run_in_threadpool
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
# 나이스 바이트 계산 기준 상한. 교사가 이보다 큰 값을 지정할 수 없다.
HARD_MAX_CHAR_LIMIT = 2500
MAX_ACTIVITIES = 10
# 한 번에 처리할 수 있는 최대 학생 수 (요청 하나에 순차적으로 Claude API를
# 여러 번 호출하므로, 처리 시간/비용을 고려해 상한을 둔다).
MAX_STUDENTS_PER_BATCH = 20
# 이력 페이지에서 검색/정렬/필터 대상으로 불러오는 최대 건수. 검색·정렬·과목
# 필터·페이지네이션을 모두 이 범위 안에서 서버가 메모리상에서 처리하므로,
# 이 건수를 넘는 오래된 이력은 검색/필터에 걸리지 않는다. 교사 한 명의
# 실사용 규모(수년치 누적 기준)를 넉넉히 덮도록 여유 있게 잡았다.
HISTORY_FETCH_LIMIT = 5000

# 과목명을 프롬프트에 직접 넣으면 요청마다 시스템 프롬프트가 달라져 프롬프트
# 캐싱이 깨지므로, 과목은 시스템 프롬프트가 아닌 사용자 프롬프트 쪽에 담는다.
SYSTEM_PROMPT = """당신은 대한민국 고등학교 교사입니다. 담당 교과목 학생의 교과 세부능력 및 특기사항(세특)을 작성하는 것을 돕는 보조 도구입니다.
다음 조건을 반드시 지켜 작성하세요.
1. 문장은 종결형 어미(~함, ~임, ~음 등 명사형 종결)로 끝맺습니다. 개조식이 아닌 서술형 문장으로 씁니다.
2. 교과 성취 수준은 문장의 깊이·어조·강조할 역량 수준을 정하는 참고 자료로만 사용합니다. "A 수준", "성취도가 우수함", "높은 성취 수준을 보임"처럼 성취 수준 자체를 문장에 직접 언급하지 않고, 학습 내용과 성장 가능성 서술에 자연스럽게 녹여냅니다.
3. 제공된 활동 관찰 자료를 근거로 학생의 수행 특기사항을 구체적으로 서술합니다. 근거 없는 내용을 추가하거나 과장하지 않습니다.
4. 긍정적인 내용만 서술합니다.
5. 서술 순서는 반드시 "교과 성취 수준에 대한 서술(직접 언급 없이) → 수행 특기사항 → 교과 역량 → 수업 태도" 순서를 따릅니다.
6. 담당 교사가 학생을 직접 관찰하고 평가하는 입장에서 서술합니다. 학생을 주어로 한 3인칭 시점을 사용하며(1인칭 표현 금지), 학생이 스스로 쓴 자기소개서나 소감문처럼 들리지 않도록 합니다.
7. 입력된 활동이 여러 개이더라도 활동별로 나누어 쓰지 않고, 이를 모두 종합해 하나의 통일된 문단으로 작성합니다.
8. 전체 바이트 수(나이스 바이트 계산 기준, 공백 포함)는 사용자가 지정한 최소/최대 바이트 범위를 반드시 지켜 작성합니다. 나이스 바이트는 한글 등 2바이트 문자 1자당 약 3바이트, 영문/숫자/공백 등 1바이트 문자 1자당 1바이트로 계산되므로, 목표 바이트를 대략 3으로 나눈 한글 글자 수를 기준으로 분량을 가늠해 작성합니다. 범위를 벗어나면 다시 작성해야 하므로, 목표 범위의 중간값에 맞춰 여유 있게 작성하세요.
9. 결과는 반드시 result라는 문자열 하나만 가진 JSON으로 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""

# "글자수 다듬기"(이미 작성된 문구의 분량만 조정) 전용 시스템 프롬프트.
# 새로 작성하는 게 아니라 기존 문구를 다듬는 것이므로, SYSTEM_PROMPT와 달리
# 서술 순서·시점 등 생기부 작성 규칙 대신 "원문 최대한 유지"를 최우선으로 둔다.
ADJUST_SYSTEM_PROMPT = """당신은 대한민국 고등학교 교사가 이미 작성한 생기부(세부능력 및 특기사항 등) 문구의 분량만 다듬어 주는 보조 도구입니다.
다음 조건을 반드시 지켜 작성하세요.
1. 원문의 내용, 사실 관계, 어조, 문체, 서술 순서를 최대한 그대로 유지합니다. 새로운 사실이나 활동을 지어내거나 임의로 삭제하지 않습니다.
2. 분량을 줄여야 하면 중복되거나 부수적인 수식어·표현을 다듬고, 늘려야 하면 이미 서술된 내용을 더 구체적으로 풀어 쓰는 방식으로 조정합니다. 문장 구조나 핵심 내용 자체를 임의로 바꾸지 않습니다.
3. 문장은 종결형 어미(~함, ~임, ~음 등 명사형 종결)로 끝맺는 서술형 문체를 유지합니다.
4. 전체 바이트 수(나이스 바이트 계산 기준, 공백 포함)는 사용자가 지정한 최소/최대 바이트 범위를 반드시 지켜 작성합니다. 나이스 바이트는 한글 등 2바이트 문자 1자당 약 3바이트, 영문/숫자/공백 등 1바이트 문자 1자당 1바이트로 계산되므로, 목표 바이트를 대략 3으로 나눈 한글 글자 수를 기준으로 분량을 가늠해 작성합니다. 범위를 벗어나면 다시 작성해야 하므로, 목표 범위의 중간값에 맞춰 여유 있게 작성하세요.
5. 결과는 반드시 result라는 문자열 하나만 가진 JSON으로 출력하고, 다른 설명이나 머리말은 덧붙이지 않습니다."""

# 첫 시도에서 목표 바이트 범위를 벗어나면 재시도하는 최대 횟수(최초 시도 제외).
MAX_LENGTH_RETRIES = 2

# 글자수 다듬기 결과를 생성 이력(generations)에 함께 남길 때 쓰는 고정 값.
# 다듬기 요청에는 학번이 없어 student_label을 이 문구로 대신하고, category로
# 일반 생성 이력과 구분해 이력 화면에서 알아볼 수 있게 한다.
ADJUST_HISTORY_STUDENT_LABEL = "(글자수 다듬기)"
ADJUST_HISTORY_CATEGORY = "글자수 다듬기"

STUDENT_ID_KEY_RE = re.compile(r"^student_id__(\d+)$")


def _build_user_prompt(
    student_id: str,
    subject: str,
    academic_achievement: str,
    activities: list[tuple[list[str], str]],
    min_char_limit: int,
    max_char_limit: int,
) -> str:
    lines = [f"학번: {student_id}", f"담당 교과목: {subject}"]
    if academic_achievement:
        lines.append(f"교과 성취 수준: {academic_achievement}")
    for index, (criteria, text) in enumerate(activities, start=1):
        labels = ", ".join(criterion_label(subject, criterion) for criterion in criteria)
        lines.append(f"[활동{index}] 성취기준: {labels}")
        lines.append(f"[활동{index}] 관찰 자료: {text}")
    lines.append(
        f"\n위 교과 성취 수준과 활동 {len(activities)}개의 관찰 자료를 모두 반영해, "
        f"하나의 세부능력 및 특기사항 문단을 작성해 주세요. "
        f"목표 바이트 수: 공백 포함 {min_char_limit}바이트 이상 {max_char_limit}바이트 이하 (나이스 바이트 계산 기준)."
    )
    return "\n".join(lines)


# 목표 범위를 살짝 벗어나도 재시도 없이 받아들이는 허용 오차(최대 바이트 기준
# 비율). 재시도 1회마다 API 호출이 그대로 늘어나므로(비용 상승), 큰 목표
# 바이트에서 범위를 근소하게 벗어난 경우까지 매번 다시 쓰게 하면 비용 대비
# 이득이 적다.
LENGTH_TOLERANCE_RATIO = 0.05


def _is_length_acceptable(char_count: int, min_char_limit: int, max_char_limit: int) -> bool:
    """목표 범위 안이거나, 허용 오차(LENGTH_TOLERANCE_RATIO) 이내로 벗어났으면
    재시도 없이 받아들인다. 상한은 나이스 입력 필드 제한(HARD_MAX_CHAR_LIMIT)을
    넘지 않도록 클램프한다."""
    tolerance = max(1, round(max_char_limit * LENGTH_TOLERANCE_RATIO))
    lower = max(1, min_char_limit - tolerance)
    upper = min(max_char_limit + tolerance, HARD_MAX_CHAR_LIMIT)
    return lower <= char_count <= upper


def _build_length_retry_prompt(char_count: int, min_char_limit: int, max_char_limit: int) -> str:
    """직전 결과가 목표 바이트 범위를 벗어났을 때, 같은 대화의 다음 사용자
    메시지로 보낼 재작성 요청 문구를 만든다."""
    direction = "줄여서" if char_count > max_char_limit else "늘려서"
    return (
        f"방금 작성한 문장은 나이스 바이트 계산 기준으로 {char_count}바이트입니다. "
        f"목표 범위({min_char_limit}~{max_char_limit}바이트)를 벗어났으니, "
        f"내용과 문체는 유지하되 분량만 {direction} 다시 작성해 주세요. "
        f"결과는 이전과 동일하게 result 하나만 가진 JSON으로 출력하세요."
    )


def _build_adjust_user_prompt(input_text: str, min_char_limit: int, max_char_limit: int) -> str:
    """"글자수 다듬기" 첫 요청에 보낼 사용자 프롬프트를 만든다."""
    current_count = neis_byte_count(input_text)
    return (
        "다음은 이미 작성된 생기부 문구입니다.\n\n"
        f"{input_text}\n\n"
        f"현재 분량은 나이스 바이트 계산 기준으로 {current_count}바이트입니다. "
        f"내용과 문체는 유지하되, 분량만 {min_char_limit}~{max_char_limit}바이트 범위에 맞게 다시 작성해 주세요."
    )


def _is_unlimited(profile: dict) -> bool:
    return profile["role"] == "admin"


def _clamp_char_limits(min_raw: str, max_raw: str) -> tuple[int, int] | None:
    """입력값을 검증만 하고 조용히 바꾸지 않는다. 최대 바이트가
    HARD_MAX_CHAR_LIMIT를 넘거나 최소가 최대보다 크면, 값을 임의로 깎지
    않고 None을 반환해 호출 쪽에서 오류로 처리하도록 한다."""
    try:
        min_limit = int(min_raw)
        max_limit = int(max_raw)
    except (TypeError, ValueError):
        return None
    if min_limit < 1 or max_limit < 1:
        return None
    if max_limit > HARD_MAX_CHAR_LIMIT:
        return None
    if min_limit > max_limit:
        return None
    return min_limit, max_limit


def _empty_student(index: int = 0) -> dict:
    return {
        "index": index,
        "student_id": "",
        "subject": "",
        "academic_achievement": "",
        "activities": [{"criteria": [], "text": ""}, {"criteria": [], "text": ""}],
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
        # 활동 1개당 성취기준을 여러 개 선택할 수 있어, 화면(JS)에서 콤마로
        # 이어붙인 문자열 하나로 보내온다. 여기서 다시 리스트로 풀어낸다.
        criteria_values = form.getlist(f"activity_criterion__{index}")
        text_values = form.getlist(f"activity_text__{index}")
        activities = [
            {"criteria": [c for c in str(criterion).split(",") if c], "text": str(text)}
            for criterion, text in zip(criteria_values, text_values)
        ][:MAX_ACTIVITIES]
        students.append(
            {
                "index": index,
                "student_id": str(form.get(f"student_id__{index}", "")).strip(),
                "subject": str(form.get(f"subject__{index}", "")).strip(),
                "academic_achievement": str(form.get(f"academic_achievement__{index}", "")).strip(),
                "activities": activities or [{"criteria": [], "text": ""}],
            }
        )
    return students or [_empty_student()]


def _teacher_subjects(profile: dict) -> list[str]:
    """이 교사가 회원가입/계정 설정에서 등록한 과목 목록을 반환한다. 아직 아무
    과목도 등록하지 않은 계정(과거 가입자 등)은 하위 호환을 위해 전체 과목
    목록을 대신 사용한다."""
    subjects = profile.get("subjects") or []
    return subjects if subjects else get_subjects()


def _parse_students(
    form: FormData, allowed_subjects: list[str] | None = None
) -> tuple[list[dict], list[dict]]:
    """생성 요청용: 학번/과목/활동이 모두 채워진 학생만 골라, 활동을
    (성취기준, 텍스트) 튜플 리스트로 변환한다. 조건을 만족하지 못해 제외된
    학생은 이유와 함께 두 번째 값(skipped)으로 따로 반환한다. allowed_subjects를
    지정하면(요청한 교사가 등록한 과목) 그 목록에 없는 과목은 거부한다."""
    allowed = set(allowed_subjects) if allowed_subjects is not None else set(get_subjects())
    students = []
    skipped = []
    for position, raw in enumerate(_parse_students_raw(form), start=1):
        label = raw["student_id"] or f"{position}번째 학생"
        if not raw["student_id"]:
            skipped.append({"label": label, "reason": "학번이 입력되지 않았습니다."})
            continue
        if raw["subject"] not in allowed:
            skipped.append({"label": label, "reason": "과목이 선택되지 않았습니다."})
            continue
        activities = [
            (activity["criteria"], activity["text"].strip())
            for activity in raw["activities"]
            if activity["text"].strip()
        ][:MAX_ACTIVITIES]
        if not activities:
            skipped.append({"label": label, "reason": "활동 관찰 자료가 입력되지 않았습니다."})
            continue
        if any(not criteria for criteria, _ in activities):
            skipped.append({"label": label, "reason": "각 활동마다 성취기준을 1개 이상 선택해야 합니다."})
            continue
        all_criteria = [criterion for criteria, _ in activities for criterion in criteria]
        if len(all_criteria) != len(set(all_criteria)):
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
    subjects = _teacher_subjects(current["profile"])
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
    # _dashboard_context 안에서 Supabase를 여러 번(사용량/이력/임시저장) 동기
    # 호출하므로, 전체를 스레드풀로 넘겨 이벤트 루프가 그동안 다른 요청을
    # 처리할 수 있게 한다.
    context = await run_in_threadpool(_dashboard_context, current)
    return templates.TemplateResponse(request, "dashboard.html", context)


HISTORY_SORT_FIELDS = {"created_at", "student_label", "category"}
HISTORY_PAGE_SIZES = (10, 20, 100)
DEFAULT_HISTORY_PAGE_SIZE = 20


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


def _extract_subject(category: str) -> str:
    """"{과목} · 성취기준 ..." 형식의 category 문자열에서 과목명만 뽑아낸다."""
    return (category or "").split(" · ", 1)[0]


def _filter_by_subject(history: list[dict], subject: str) -> list[dict]:
    """선택한 과목과 일치하는 이력만 남긴다. subject가 비어 있으면 전체를 반환한다."""
    if not subject:
        return history
    return [row for row in history if _extract_subject(row.get("category") or "") == subject]


def _sort_history(history: list[dict], sort: str, order: str) -> list[dict]:
    if sort not in HISTORY_SORT_FIELDS:
        sort = "created_at"
    if order not in ("asc", "desc"):
        order = "desc"
    return sorted(history, key=lambda row: row.get(sort) or "", reverse=(order == "desc"))


def _paginate_history(history: list[dict], page: int, page_size: int) -> tuple[list[dict], int, int]:
    """(현재 페이지 항목, 보정된 페이지 번호, 전체 페이지 수)를 반환한다.
    page가 1~total_pages 범위를 벗어나면 그 안으로 보정한다. page_size 자체의
    유효성 검사(허용된 값인지)는 호출하는 라우트에서 미리 처리한다."""
    total = len(history)
    total_pages = max(1, -(-total // page_size))  # 올림 나눗셈
    page = min(max(page, 1), total_pages)
    start = (page - 1) * page_size
    return history[start : start + page_size], page, total_pages


@router.get("/history")
async def history_page(
    request: Request,
    q: str = "",
    subject: str = "",
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = DEFAULT_HISTORY_PAGE_SIZE,
    current: CurrentUser = Depends(require_approved),
):
    if sort not in HISTORY_SORT_FIELDS:
        sort = "created_at"
    if order not in ("asc", "desc"):
        order = "desc"
    if page_size not in HISTORY_PAGE_SIZES:
        page_size = DEFAULT_HISTORY_PAGE_SIZE
    if subject not in get_subjects():
        subject = ""

    client = get_user_client(current["access_token"])

    def _fetch_history():
        return (
            client.table("generations")
            .select("*")
            .eq("user_id", current["user_id"])
            .order("created_at", desc=True)
            .limit(HISTORY_FETCH_LIMIT)
            .execute()
            .data
        )

    # Supabase 동기 호출을 스레드풀로 넘겨 이벤트 루프를 막지 않는다.
    history = await run_in_threadpool(_fetch_history)

    query = q.strip()
    history = _filter_history(history, query)
    history = _filter_by_subject(history, subject)
    history = _sort_history(history, sort, order)

    total = len(history)
    page_items, page, total_pages = _paginate_history(history, page, page_size)

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "profile": current["profile"],
            "history": page_items,
            "active_nav": "history",
            "q": query,
            "subject": subject,
            "subjects": get_subjects(),
            "sort": sort,
            "order": order,
            "page": page,
            "page_size": page_size,
            "page_sizes": HISTORY_PAGE_SIZES,
            "total": total,
            "total_pages": total_pages,
        },
    )


def _filter_criteria(criteria: list[tuple[str, str]], query: str) -> list[tuple[str, str]]:
    """성취기준 코드/설명에 검색어가 포함된 항목만 남긴다 (대소문자 무시)."""
    query = query.strip()
    if not query:
        return criteria
    needle = query.lower()
    return [
        (code, description)
        for code, description in criteria
        if needle in code.lower() or needle in description.lower()
    ]


@router.get("/criteria")
async def criteria_page(
    request: Request,
    subject: str = "",
    q: str = "",
    current: CurrentUser = Depends(require_approved),
):
    subjects = get_subjects()
    if subject not in subjects:
        subject = subjects[0] if subjects else ""

    query = q.strip()
    criteria = _filter_criteria(get_criteria(subject), query) if subject else []

    return templates.TemplateResponse(
        request,
        "criteria.html",
        {
            "profile": current["profile"],
            "active_nav": "criteria",
            "subjects": subjects,
            "subject": subject,
            "q": query,
            "criteria": criteria,
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
    await run_in_threadpool(
        lambda: client.table("drafts")
        .upsert({"user_id": current["user_id"], "data": draft_data})
        .execute()
    )

    context = await run_in_threadpool(
        _dashboard_context,
        current,
        notice="임시저장되었습니다.",
        students=raw_students,
        min_char_limit=min_char_raw,
        max_char_limit=max_char_raw,
    )
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/draft/clear")
async def clear_draft(request: Request, current: CurrentUser = Depends(require_approved)):
    """학생 입력 폼을 빈 상태로 초기화한다. 화면만 지우는 "모두 지우기"
    버튼(클라이언트 전용)과 달리, 저장된 임시저장 내용도 함께 지워서
    "불러오기"로도 되돌릴 수 없게 한다."""
    client = get_user_client(current["access_token"])
    await run_in_threadpool(
        lambda: client.table("drafts").delete().eq("user_id", current["user_id"]).execute()
    )

    context = await run_in_threadpool(
        _dashboard_context, current, notice="입력 내용을 초기화했습니다."
    )
    return templates.TemplateResponse(request, "dashboard.html", context)


def _save_adjust_char_limits(current: CurrentUser, min_char_limit: int, max_char_limit: int) -> None:
    """글자수 다듬기에서 마지막으로 사용한 목표 바이트 범위를 drafts 테이블에
    저장해, 다음에 이 화면을 열었을 때도 매번 기본값(600~700)으로 되돌아가지
    않고 그대로 유지되게 한다. 대시보드 임시저장과 같은 행(user_id 1건)을
    공유하므로, 기존에 저장된 다른 키(students 등)는 건드리지 않는다."""
    draft = _load_draft(current) or {}
    draft["adjust_min_char_limit"] = min_char_limit
    draft["adjust_max_char_limit"] = max_char_limit
    client = get_user_client(current["access_token"])
    try:
        client.table("drafts").upsert({"user_id": current["user_id"], "data": draft}).execute()
    except Exception:
        pass


def _adjust_context(
    current: CurrentUser,
    error: str | None = None,
    result: dict | None = None,
    input_text: str = "",
    min_char_limit: int | str | None = None,
    max_char_limit: int | str | None = None,
) -> dict:
    unlimited = _is_unlimited(current["profile"])
    status = ledger_status(get_service_client(), current["profile"]["email"])
    used = status["used"]
    limit = status["monthly_limit"]
    remaining = None if unlimited else max(limit - used, 0)
    reset_days = days_until_reset(status["period_start"])

    if min_char_limit is None or max_char_limit is None:
        draft = _load_draft(current) or {}
        if min_char_limit is None:
            min_char_limit = draft.get("adjust_min_char_limit") or DEFAULT_MIN_CHAR_LIMIT
        if max_char_limit is None:
            max_char_limit = draft.get("adjust_max_char_limit") or DEFAULT_MAX_CHAR_LIMIT

    return {
        "profile": current["profile"],
        "active_nav": "adjust",
        "used": used,
        "limit": limit,
        "unlimited": unlimited,
        "remaining": remaining,
        "reset_days": reset_days,
        "hard_max_char_limit": HARD_MAX_CHAR_LIMIT,
        "input_text": input_text,
        "min_char_limit": min_char_limit,
        "max_char_limit": max_char_limit,
        "error": error,
        "result": result,
    }


@router.get("/adjust")
async def adjust_page(request: Request, current: CurrentUser = Depends(require_approved)):
    context = await run_in_threadpool(_adjust_context, current)
    return templates.TemplateResponse(request, "adjust.html", context)


@router.post("/adjust")
async def adjust_length(
    request: Request,
    input_text: str = Form(...),
    min_char_limit: str = Form(str(DEFAULT_MIN_CHAR_LIMIT)),
    max_char_limit: str = Form(str(DEFAULT_MAX_CHAR_LIMIT)),
    current: CurrentUser = Depends(require_approved),
):
    input_text = input_text.strip()

    if not input_text:
        context = await run_in_threadpool(
            _adjust_context,
            current,
            error="다듬을 문구를 입력해 주세요.",
            input_text=input_text,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "adjust.html", context)

    char_limits = _clamp_char_limits(min_char_limit, max_char_limit)
    if char_limits is None:
        context = await run_in_threadpool(
            _adjust_context,
            current,
            error=f"바이트 설정이 올바르지 않습니다. 최대 바이트는 {HARD_MAX_CHAR_LIMIT}바이트를 넘을 수 없고, "
            "최소 바이트는 최대 바이트보다 작아야 합니다.",
            input_text=input_text,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "adjust.html", context)
    min_char_limit_value, max_char_limit_value = char_limits
    await run_in_threadpool(_save_adjust_char_limits, current, min_char_limit_value, max_char_limit_value)

    if contains_rrn(input_text):
        context = await run_in_threadpool(
            _adjust_context,
            current,
            error="입력 내용에 주민등록번호로 의심되는 패턴이 포함되어 있어 요청을 차단했습니다. "
            "민감정보를 제거한 뒤 다시 시도해 주세요.",
            input_text=input_text,
            min_char_limit=min_char_limit_value,
            max_char_limit=max_char_limit_value,
        )
        return templates.TemplateResponse(request, "adjust.html", context)

    if not settings.anthropic_api_key:
        context = await run_in_threadpool(
            _adjust_context,
            current,
            error="관리자가 아직 Anthropic API 키를 설정하지 않았습니다. 잠시 후 다시 시도해 주세요.",
            input_text=input_text,
            min_char_limit=min_char_limit_value,
            max_char_limit=max_char_limit_value,
        )
        return templates.TemplateResponse(request, "adjust.html", context)

    service_client = get_service_client()
    unlimited = _is_unlimited(current["profile"])
    status = await run_in_threadpool(ledger_status, service_client, current["profile"]["email"])
    used = status["used"]
    limit = status["monthly_limit"]

    # 글자수 다듬기도 생성과 마찬가지로 Claude API를 호출하므로, 같은 사용
    # 한도(생성 건수)에 함께 카운트한다. 별도로 두면 한도를 우회하는 수단이
    # 될 수 있기 때문이다.
    if not unlimited and used >= limit:
        context = await run_in_threadpool(
            _adjust_context,
            current,
            error=f"이번 달 사용 한도({limit}건)를 이미 모두 사용했습니다. 다음 리셋일까지 기다려 주세요.",
            input_text=input_text,
            min_char_limit=min_char_limit_value,
            max_char_limit=max_char_limit_value,
        )
        return templates.TemplateResponse(request, "adjust.html", context)

    from anthropic import Anthropic

    anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
    client = get_user_client(current["access_token"])

    def _run():
        user_prompt = _build_adjust_user_prompt(input_text, min_char_limit_value, max_char_limit_value)
        try:
            result_text, char_count = _call_claude_with_length_retry(
                anthropic_client, ADJUST_SYSTEM_PROMPT, user_prompt, min_char_limit_value, max_char_limit_value
            )
        except Exception as exc:
            error_detail = str(exc) or repr(exc)
            return {"error": f"다듬기 중 오류가 발생했습니다: {error_detail}"}

        # 생성 이력과 동일한 테이블에 남긴다. 다듬기 요청에는 학번이 없으므로
        # student_label은 고정 문구로 채우고, category로 생성 이력과 구분한다.
        client.table("generations").insert(
            {
                "user_id": current["user_id"],
                "student_label": ADJUST_HISTORY_STUDENT_LABEL,
                "category": ADJUST_HISTORY_CATEGORY,
                "input_text": input_text,
                "output_text": result_text,
                "model": settings.anthropic_model,
            }
        ).execute()

        if not unlimited:
            record_generation(service_client, current["profile"]["email"], used)
        return {
            "text": result_text,
            "count": char_count,
            "min_char_limit": min_char_limit_value,
            "max_char_limit": max_char_limit_value,
        }

    run_result = await run_in_threadpool(_run)

    context = await run_in_threadpool(
        _adjust_context,
        current,
        error=run_result.get("error"),
        result=None if "error" in run_result else run_result,
        input_text=input_text,
        min_char_limit=min_char_limit_value,
        max_char_limit=max_char_limit_value,
    )
    return templates.TemplateResponse(request, "adjust.html", context)


def _call_claude_with_length_retry(
    anthropic_client,
    system_prompt: str,
    user_prompt: str,
    min_char_limit: int,
    max_char_limit: int,
) -> tuple[str, int]:
    """system_prompt/user_prompt로 Claude를 호출하고, 결과가 목표 바이트
    범위를 벗어나면 같은 대화를 이어가며 최대 MAX_LENGTH_RETRIES번 재시도한다.
    (결과 텍스트, 바이트 수)를 반환한다. 실패하면 예외를 그대로 던진다 —
    호출하는 쪽에서 상황에 맞는 에러 메시지로 감싼다."""
    messages = [{"role": "user", "content": user_prompt}]

    for attempt in range(MAX_LENGTH_RETRIES + 1):
        response = anthropic_client.messages.create(
            model=settings.anthropic_model,
            # thinking을 꺼서 사고 과정에 토큰을 쓰지 않으므로, 결과 텍스트
            # (최대 HARD_MAX_CHAR_LIMIT 바이트)만 감당하면 되는 수준으로
            # max_tokens를 잡는다. 한글은 바이트당 토큰 수가 영어보다 크므로
            # 여유를 두었다.
            max_tokens=4000,
            thinking={"type": "disabled"},
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
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
            messages=messages,
        )
        text_block = next((block for block in response.content if block.type == "text"), None)
        if text_block is None:
            raise RuntimeError(
                f"Claude 응답에서 결과 텍스트를 찾지 못했습니다 (stop_reason={response.stop_reason})."
            )
        data = json.loads(text_block.text)
        result_text = str(data["result"])
        char_count = neis_byte_count(result_text)

        if _is_length_acceptable(char_count, min_char_limit, max_char_limit) or attempt == MAX_LENGTH_RETRIES:
            return result_text, char_count

        # 목표 바이트 범위를 허용 오차 이상으로 벗어났으면, 같은 대화를
        # 이어가며 분량만 조정해 다시 작성해 달라고 요청한다(활동 내용을
        # 다시 보낼 필요 없이 직전 결과를 근거로 삼아 조정).
        messages.append({"role": "assistant", "content": text_block.text})
        messages.append(
            {
                "role": "user",
                "content": _build_length_retry_prompt(char_count, min_char_limit, max_char_limit),
            }
        )
    raise AssertionError("unreachable")  # pragma: no cover


def _generate_and_record_result(
    anthropic_client,
    client,
    service_client,
    current: CurrentUser,
    student: dict,
    user_prompt: str,
    min_char_limit: int,
    max_char_limit: int,
    duplicate_student: bool,
    unlimited: bool,
    used: int,
) -> dict:
    """한 학생에 대해 Claude 호출(+분량 재시도) → 결과 저장 → 사용량 기록까지
    한 번에 처리하는 동기 함수. 학생 1명당 최대 MAX_LENGTH_RETRIES+1회의
    Claude API 호출 + Supabase 쓰기가 일어나는, 이 라우트에서 가장 오래
    걸리는 블로킹 구간이라 스레드풀로 통째로 넘기기 위해 분리했다.
    실패 시 {"student_id":..., "error":...}를, 성공 시 결과 dict를 반환한다."""
    try:
        result_text, char_count = _call_claude_with_length_retry(
            anthropic_client, SYSTEM_PROMPT, user_prompt, min_char_limit, max_char_limit
        )
    except Exception as exc:
        # 일부 예외(예: 빈 StopIteration)는 str()이 빈 문자열이라 메시지가
        # 안 보일 수 있으므로, 비어 있으면 repr()로 대체해 항상 정보를 남긴다.
        error_detail = str(exc) or repr(exc)
        return {
            "student_id": student["student_id"],
            "error": f"생성 중 오류가 발생했습니다: {error_detail}",
        }

    # 기존 이력을 덮어쓰지 않고 새 행으로 추가한다.
    client.table("generations").insert(
        {
            "user_id": current["user_id"],
            "student_label": student["student_id"],
            "category": f"{student['subject']} · 성취기준 "
            + "/".join(criterion for criteria, _ in student["activities"] for criterion in criteria),
            "input_text": user_prompt,
            "output_text": result_text,
            "model": settings.anthropic_model,
        }
    ).execute()

    if not unlimited:
        record_generation(service_client, current["profile"]["email"], used)

    return {
        "student_id": student["student_id"],
        "text": result_text,
        "count": char_count,
        "min_char_limit": min_char_limit,
        "max_char_limit": max_char_limit,
        "duplicate_student": duplicate_student,
    }


@router.post("/generate")
async def generate(request: Request, current: CurrentUser = Depends(require_approved)):
    form = await request.form()
    raw_students = _parse_students_raw(form)
    students, skipped = _parse_students(form, _teacher_subjects(current["profile"]))

    min_char_raw = form.get("min_char_limit", str(DEFAULT_MIN_CHAR_LIMIT))
    max_char_raw = form.get("max_char_limit", str(DEFAULT_MAX_CHAR_LIMIT))
    char_limits = _clamp_char_limits(min_char_raw, max_char_raw)

    if not students:
        if skipped:
            reasons = "; ".join(f"{item['label']}: {item['reason']}" for item in skipped)
            error_message = f"입력한 학생 정보가 올바르지 않아 생성할 수 없습니다. ({reasons})"
        else:
            error_message = "학생을 최소 1명 이상, 각 학생마다 학번/과목/활동 내용을 입력해 주세요."
        context = await run_in_threadpool(
            _dashboard_context,
            current,
            error=error_message,
            students=raw_students,
            min_char_limit=min_char_raw,
            max_char_limit=max_char_raw,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if char_limits is None:
        context = await run_in_threadpool(
            _dashboard_context,
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
    status = await run_in_threadpool(ledger_status, service_client, current["profile"]["email"])
    used = status["used"]
    limit = status["monthly_limit"]
    unlimited = _is_unlimited(current["profile"])

    if not unlimited and used + len(students) > limit:
        context = await run_in_threadpool(
            _dashboard_context,
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
        context = await run_in_threadpool(
            _dashboard_context,
            current,
            error="입력 내용에 주민등록번호로 의심되는 패턴이 포함되어 있어 요청을 차단했습니다. "
            "민감정보를 제거한 뒤 다시 시도해 주세요.",
            students=raw_students,
            min_char_limit=min_char_limit,
            max_char_limit=max_char_limit,
        )
        return templates.TemplateResponse(request, "dashboard.html", context)

    if not settings.anthropic_api_key:
        context = await run_in_threadpool(
            _dashboard_context,
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
    existing_labels = await run_in_threadpool(_existing_student_labels, current)

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

        # 학생 1명당 최대 MAX_LENGTH_RETRIES+1회의 Claude API 호출과
        # Supabase 쓰기가 일어나는 구간을 스레드풀로 넘긴다. 학생 수만큼
        # 여전히 순차 처리되지만(기존과 동일한 처리 순서), 각 호출이 진행되는
        # 동안 이벤트 루프가 막히지 않아 다른 사용자의 요청을 함께 처리할 수
        # 있다.
        result_item = await run_in_threadpool(
            _generate_and_record_result,
            anthropic_client,
            client,
            service_client,
            current,
            student,
            user_prompt,
            min_char_limit,
            max_char_limit,
            duplicate_student,
            unlimited,
            used,
        )
        results.append(result_item)

        if "error" not in result_item:
            existing_labels.add(student["student_id"])
            if not unlimited:
                used += 1

    skip_error = None
    if skipped:
        reasons = "; ".join(f"{item['label']}: {item['reason']}" for item in skipped)
        skip_error = f"다음 학생은 정보가 올바르지 않아 생성에서 제외되었습니다. ({reasons})"

    context = await run_in_threadpool(
        _dashboard_context,
        current,
        error=skip_error,
        result=results,
        students=raw_students,
        min_char_limit=min_char_limit,
        max_char_limit=max_char_limit,
    )
    return templates.TemplateResponse(request, "dashboard.html", context)
