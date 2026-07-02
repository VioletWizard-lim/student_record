from starlette.datastructures import FormData

from app.generation import _parse_students


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
    students = _parse_students(form)
    assert len(students) == 1
    assert students[0]["student_id"] == "10101"
    assert students[0]["activities"] == [("12정01-01", "관찰 내용 1")]


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
    students = _parse_students(form)
    assert [s["student_id"] for s in students] == ["10101", "10103"]


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
    assert _parse_students(form) == []


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
    assert _parse_students(form) == []
