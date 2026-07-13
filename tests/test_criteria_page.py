from app.generation import _filter_criteria

SAMPLE = [
    ("12정01-01", "유무선 네트워크의 특성을 이해하고, 네트워크 환경을 구성한다."),
    ("12정01-02", "사물인터넷의 구성과 동작 원리를 분석한다."),
    ("12정02-01", "디지털 데이터 압축의 개념과 필요성을 이해한다."),
]


def test_filter_criteria_by_code():
    result = _filter_criteria(SAMPLE, "01-02")
    assert [code for code, _ in result] == ["12정01-02"]


def test_filter_criteria_by_description_case_insensitive():
    result = _filter_criteria(SAMPLE, "압축")
    assert [code for code, _ in result] == ["12정02-01"]


def test_filter_criteria_empty_query_returns_all():
    assert _filter_criteria(SAMPLE, "   ") == SAMPLE


def test_filter_criteria_no_match_returns_empty():
    assert _filter_criteria(SAMPLE, "존재하지않음") == []
