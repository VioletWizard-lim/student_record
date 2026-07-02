from app.generation import _filter_history, _sort_history

SAMPLE = [
    {"created_at": "2026-06-01T10:00:00", "student_label": "10101", "category": "정보 · 성취기준 12정01-01", "output_text": "네트워크 관련 세특 문구"},
    {"created_at": "2026-06-05T09:00:00", "student_label": "10203", "category": "정보 · 성취기준 12정03-02", "output_text": "알고리즘 관련 세특 문구"},
    {"created_at": "2026-06-03T12:00:00", "student_label": "10102", "category": "정보 · 성취기준 12정02-01", "output_text": "압축 관련 세특 문구"},
]


def test_filter_history_by_student_label():
    result = _filter_history(SAMPLE, "10203")
    assert [row["student_label"] for row in result] == ["10203"]


def test_filter_history_by_output_text_case_insensitive():
    result = _filter_history(SAMPLE, "알고리즘")
    assert len(result) == 1
    assert result[0]["student_label"] == "10203"


def test_filter_history_empty_query_returns_all():
    assert _filter_history(SAMPLE, "   ") == SAMPLE


def test_filter_history_no_match_returns_empty():
    assert _filter_history(SAMPLE, "존재하지않음") == []


def test_sort_history_by_created_at_desc_default():
    result = _sort_history(SAMPLE, "created_at", "desc")
    assert [row["student_label"] for row in result] == ["10203", "10102", "10101"]


def test_sort_history_by_student_label_asc():
    result = _sort_history(SAMPLE, "student_label", "asc")
    assert [row["student_label"] for row in result] == ["10101", "10102", "10203"]


def test_sort_history_invalid_field_falls_back_to_created_at():
    result = _sort_history(SAMPLE, "not_a_field", "desc")
    assert [row["student_label"] for row in result] == ["10203", "10102", "10101"]


def test_sort_history_invalid_order_falls_back_to_desc():
    result = _sort_history(SAMPLE, "created_at", "sideways")
    assert [row["student_label"] for row in result] == ["10203", "10102", "10101"]
