from starlette.datastructures import FormData

from app.generation import _parse_students, _parse_students_raw, _teacher_subjects
from app.subject_criteria import get_subjects


def test_parses_single_student():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용 1"),
        ]
    )
    students, skipped = _parse_students(form)
    assert len(students) == 1
    assert students[0]["student_id"] == "10101"
    assert students[0]["activities"] == [(["12정01-01"], "관찰 내용 1")]
    assert skipped == []


def test_parses_activity_with_multiple_criteria():
    # 활동 1개에 성취기준을 여러 개 선택하면, 화면(JS)에서 콤마로 이어붙여
    # 보내온다.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01,12정01-02"),
            ("activity_text__0", "관찰 내용 1"),
        ]
    )
    students, skipped = _parse_students(form)
    assert len(students) == 1
    assert students[0]["activities"] == [(["12정01-01", "12정01-02"], "관찰 내용 1")]
    assert skipped == []


def test_parses_multiple_students_with_gaps():
    # 인덱스가 연속적이지 않아도(중간 학생 삭제 후) 정상 처리되어야 한다.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용 1"),
            ("student_id__2", "10103"),
            ("subject__2", "정보"),
            ("academic_achievement__2", "B"),
            ("activity_criterion__2", "12정01-02"),
            ("activity_text__2", "관찰 내용 3"),
        ]
    )
    students, skipped = _parse_students(form)
    assert [s["student_id"] for s in students] == ["10101", "10103"]
    assert skipped == []


def test_skips_student_with_no_activities():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "   "),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert skipped == [{"label": "10101", "reason": "활동 관찰 자료가 입력되지 않았습니다."}]


def test_skips_student_with_activity_missing_criteria():
    # 관찰 자료는 입력했지만 성취기준을 하나도 선택하지 않은 경우.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", ""),
            ("activity_text__0", "관찰 내용 1"),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert skipped == [{"label": "10101", "reason": "각 활동마다 성취기준을 1개 이상 선택해야 합니다."}]


def test_skips_student_with_invalid_subject():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "존재하지않는과목"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용"),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert skipped == [{"label": "10101", "reason": "과목이 선택되지 않았습니다."}]


def test_skips_student_with_no_student_id():
    form = FormData(
        [
            ("student_id__0", ""),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용"),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert skipped == [{"label": "1번째 학생", "reason": "학번이 입력되지 않았습니다."}]


def test_partial_skip_keeps_valid_students():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용 1"),
            ("student_id__1", ""),
            ("subject__1", "정보"),
            ("academic_achievement__1", ""),
            ("activity_criterion__1", "12정01-02"),
            ("activity_text__1", "관찰 내용 2"),
        ]
    )
    students, skipped = _parse_students(form)
    assert [s["student_id"] for s in students] == ["10101"]
    assert skipped == [{"label": "2번째 학생", "reason": "학번이 입력되지 않았습니다."}]


def test_skips_student_with_duplicate_criteria():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용 1"),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", "관찰 내용 2"),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert len(skipped) == 1
    assert skipped[0]["label"] == "10101"
    assert "중복" in skipped[0]["reason"]


def test_skips_student_with_duplicate_criteria_across_multi_select_activities():
    # 활동1에서 복수 선택한 성취기준 중 하나가 활동2에서 또 선택된 경우도
    # 중복으로 잡아야 한다.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "정보"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "12정01-01,12정01-02"),
            ("activity_text__0", "관찰 내용 1"),
            ("activity_criterion__0", "12정01-02"),
            ("activity_text__0", "관찰 내용 2"),
        ]
    )
    students, skipped = _parse_students(form)
    assert students == []
    assert len(skipped) == 1
    assert "중복" in skipped[0]["reason"]


def test_raw_parse_keeps_incomplete_student_for_draft():
    # 임시저장 시에는 비어 있거나 잘못된 값도 그대로 보존해야 한다.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", ""),
            ("academic_achievement__0", ""),
            ("activity_criterion__0", "12정01-01"),
            ("activity_text__0", ""),
        ]
    )
    students = _parse_students_raw(form)
    assert len(students) == 1
    assert students[0]["index"] == 0
    assert students[0]["student_id"] == "10101"
    assert students[0]["subject"] == ""
    assert students[0]["activities"] == [{"criteria": ["12정01-01"], "text": ""}]


def test_raw_parse_empty_form_returns_single_empty_student():
    students = _parse_students_raw(FormData([]))
    assert len(students) == 1
    assert students[0]["index"] == 0
    assert students[0]["student_id"] == ""


def test_teacher_subjects_returns_registered_subjects():
    profile = {"subjects": ["한국사1"]}
    assert _teacher_subjects(profile) == ["한국사1"]


def test_teacher_subjects_falls_back_to_all_when_empty():
    # 아직 과목을 등록하지 않은 계정(과거 가입자 등)은 전체 과목 목록을 쓴다.
    profile = {"subjects": []}
    assert _teacher_subjects(profile) == get_subjects()


def test_teacher_subjects_falls_back_when_missing():
    profile = {}
    assert _teacher_subjects(profile) == get_subjects()


def test_parse_students_rejects_subject_outside_allowed_list():
    # 교사가 등록하지 않은 과목으로는 생성 요청을 보낼 수 없어야 한다.
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "한국사1"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "10한사1-01-01"),
            ("activity_text__0", "관찰 내용 1"),
        ]
    )
    students, skipped = _parse_students(form, allowed_subjects=["정보"])
    assert students == []
    assert skipped == [{"label": "10101", "reason": "과목이 선택되지 않았습니다."}]


def test_parse_students_accepts_subject_within_allowed_list():
    form = FormData(
        [
            ("student_id__0", "10101"),
            ("subject__0", "한국사1"),
            ("academic_achievement__0", "A"),
            ("activity_criterion__0", "10한사1-01-01"),
            ("activity_text__0", "관찰 내용 1"),
        ]
    )
    students, skipped = _parse_students(form, allowed_subjects=["한국사1"])
    assert len(students) == 1
    assert skipped == []
