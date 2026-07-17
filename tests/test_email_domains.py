from app.email_domains import is_allowed_education_email, is_generic_gov_email


def test_allows_bare_office_domain():
    assert is_allowed_education_email("teacher@ice.go.kr")


def test_allows_school_subdomain():
    assert is_allowed_education_email("teacher@somehigh.ice.go.kr")


def test_rejects_non_education_domain():
    assert not is_allowed_education_email("teacher@gmail.com")


def test_rejects_lookalike_domain():
    assert not is_allowed_education_email("teacher@ice.go.kr.evil.com")


def test_is_case_insensitive():
    assert is_allowed_education_email("teacher@ICE.GO.KR")


def test_rejects_missing_at_sign():
    assert not is_allowed_education_email("not-an-email")


def test_allows_gyeongbuk_dot_kr_domain():
    # 경상북도교육청은 다른 교육청과 달리 .go.kr이 아니라 .kr을 쓴다.
    assert is_allowed_education_email("teacher@gbe.kr")
    assert is_allowed_education_email("teacher@somehigh.gbe.kr")


def test_rejects_incorrect_gyeongbuk_go_kr_domain():
    assert not is_allowed_education_email("teacher@gbe.go.kr")


def test_allows_korea_dot_kr_gov_domain():
    assert is_allowed_education_email("civil.servant@korea.kr")


def test_is_generic_gov_email_flags_korea_dot_kr():
    assert is_generic_gov_email("civil.servant@korea.kr")


def test_is_generic_gov_email_does_not_flag_education_office_domain():
    assert not is_generic_gov_email("teacher@ice.go.kr")
