from app.generation import (
    HARD_MAX_CHAR_LIMIT,
    _build_adjust_user_prompt,
    _build_length_retry_prompt,
    _is_length_acceptable,
)


def test_retry_prompt_asks_to_shorten_when_over_max():
    prompt = _build_length_retry_prompt(char_count=2300, min_char_limit=1000, max_char_limit=2000)
    assert "2300바이트" in prompt
    assert "1000~2000바이트" in prompt
    assert "줄여서" in prompt


def test_retry_prompt_asks_to_lengthen_when_under_min():
    prompt = _build_length_retry_prompt(char_count=400, min_char_limit=600, max_char_limit=700)
    assert "400바이트" in prompt
    assert "늘려서" in prompt


def test_length_acceptable_within_range():
    assert _is_length_acceptable(1600, min_char_limit=1500, max_char_limit=1800) is True


def test_length_acceptable_slightly_over_max_within_tolerance():
    # max=1800 기준 허용 오차는 round(1800*0.05)=90 -> 상한 1890까지 허용
    assert _is_length_acceptable(1850, min_char_limit=1500, max_char_limit=1800) is True


def test_length_acceptable_slightly_under_min_within_tolerance():
    # 하한 1500-90=1410까지 허용
    assert _is_length_acceptable(1450, min_char_limit=1500, max_char_limit=1800) is True


def test_length_not_acceptable_far_over_tolerance():
    assert _is_length_acceptable(1950, min_char_limit=1500, max_char_limit=1800) is False


def test_length_not_acceptable_far_under_tolerance():
    assert _is_length_acceptable(1300, min_char_limit=1500, max_char_limit=1800) is False


def test_length_tolerance_scales_with_smaller_default_range():
    # max=700 기준 허용 오차는 round(700*0.05)=35 -> 상한 735까지 허용, 740은 불허
    assert _is_length_acceptable(730, min_char_limit=600, max_char_limit=700) is True
    assert _is_length_acceptable(740, min_char_limit=600, max_char_limit=700) is False


def test_length_tolerance_upper_bound_clamped_to_hard_limit():
    max_char_limit = HARD_MAX_CHAR_LIMIT - 20
    assert _is_length_acceptable(HARD_MAX_CHAR_LIMIT, min_char_limit=1, max_char_limit=max_char_limit) is True
    assert (
        _is_length_acceptable(HARD_MAX_CHAR_LIMIT + 50, min_char_limit=1, max_char_limit=max_char_limit)
        is False
    )


def test_adjust_prompt_includes_original_text_and_target_range():
    prompt = _build_adjust_user_prompt("학생이 열심히 노력함.", min_char_limit=600, max_char_limit=700)
    assert "학생이 열심히 노력함." in prompt
    assert "600~700바이트" in prompt


def test_adjust_prompt_reports_current_byte_count():
    # "가"(한글 1자, 나이스 바이트 기준 3바이트) * 2 = 6바이트
    prompt = _build_adjust_user_prompt("가가", min_char_limit=100, max_char_limit=200)
    assert "6바이트" in prompt
