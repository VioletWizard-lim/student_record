from app.email_domains import is_allowed_education_email


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
