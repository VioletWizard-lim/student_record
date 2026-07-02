from app.pii import contains_rrn


def test_detects_rrn_with_dash():
    assert contains_rrn("학생 주민등록번호는 010101-3123456 입니다")


def test_detects_rrn_without_dash():
    assert contains_rrn("0101013123456")


def test_no_false_positive_on_normal_text():
    assert not contains_rrn("이 학생은 3학년 1반 15번이며 수학 성적이 우수함")


def test_checks_multiple_fields():
    assert contains_rrn("홍길동", "010101-3123456 포함된 자료")
    assert not contains_rrn("홍길동", "일반 관찰 자료입니다")
