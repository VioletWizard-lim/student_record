from app.generation import _extract_subject, _filter_by_subject, _filter_history, _paginate_history, _sort_history

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


def test_paginate_history_first_page():
    items, page, total_pages = _paginate_history(SAMPLE, page=1, page_size=2)
    assert [row["student_label"] for row in items] == ["10101", "10203"]
    assert page == 1
    assert total_pages == 2


def test_paginate_history_second_page():
    items, page, total_pages = _paginate_history(SAMPLE, page=2, page_size=2)
    assert [row["student_label"] for row in items] == ["10102"]
    assert page == 2
    assert total_pages == 2


def test_paginate_history_page_beyond_range_clamped_to_last():
    items, page, total_pages = _paginate_history(SAMPLE, page=99, page_size=2)
    assert [row["student_label"] for row in items] == ["10102"]
    assert page == 2
    assert total_pages == 2


def test_paginate_history_uses_given_order_without_resorting():
    # _paginate_history는 정렬을 하지 않고 전달받은 순서 그대로 자른다
    # (정렬은 _sort_history가 먼저 처리하고, 그 결과를 넘겨받는 구조).
    sorted_sample = _sort_history(SAMPLE, "student_label", "asc")
    items, _, _ = _paginate_history(sorted_sample, page=1, page_size=2)
    assert [row["student_label"] for row in items] == ["10101", "10102"]


def test_paginate_history_page_below_range_clamped_to_first():
    items, page, total_pages = _paginate_history(SAMPLE, page=0, page_size=2)
    assert page == 1


def test_paginate_history_page_size_larger_than_total_returns_single_page():
    items, page, total_pages = _paginate_history(SAMPLE, page=1, page_size=999)
    assert len(items) == len(SAMPLE)
    assert total_pages == 1


def test_paginate_history_empty_list_returns_single_page():
    items, page, total_pages = _paginate_history([], page=1, page_size=10)
    assert items == []
    assert page == 1
    assert total_pages == 1


def test_extract_subject_from_category():
    assert _extract_subject("정보 · 성취기준 12정01-01") == "정보"


def test_extract_subject_missing_separator_returns_whole_string():
    assert _extract_subject("영어") == "영어"


def test_extract_subject_empty_or_none_returns_empty_string():
    assert _extract_subject("") == ""
    assert _extract_subject(None) == ""


def test_filter_by_subject_matches_exact_subject():
    mixed = SAMPLE + [
        {"created_at": "2026-06-04T09:00:00", "student_label": "10104", "category": "영어 · 성취기준 10영01-01", "output_text": "영어 관련 세특 문구"}
    ]
    result = _filter_by_subject(mixed, "영어")
    assert [row["student_label"] for row in result] == ["10104"]


def test_filter_by_subject_empty_returns_all():
    assert _filter_by_subject(SAMPLE, "") == SAMPLE
