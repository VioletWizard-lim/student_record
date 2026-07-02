from starlette.datastructures import FormData

from app.generation import _parse_students, _parse_students_raw


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
    assert students[0]["activities"] == [("12정01-01", "관찰 내용 1")]
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
    assert students[0]["activities"] == [{"criterion": "12정01-01", "text": ""}]


def test_raw_parse_empty_form_returns_single_empty_student():
    students = _parse_students_raw(FormData([]))
    assert len(students) == 1
    assert students[0]["index"] == 0
    assert students[0]["student_id"] == ""
