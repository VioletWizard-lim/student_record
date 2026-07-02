from app.generation import HARD_MAX_CHAR_LIMIT, _clamp_char_limits


def test_valid_limits_within_range():
    assert _clamp_char_limits("600", "700") == (600, 700)


def test_max_exceeding_hard_limit_is_rejected_not_clamped():
    # 상한을 넘는 값을 조용히 깎지 않고 None(오류)으로 처리해야 한다.
    assert _clamp_char_limits("600", str(HARD_MAX_CHAR_LIMIT + 500)) is None


def test_max_equal_to_hard_limit_is_valid():
    assert _clamp_char_limits("1", str(HARD_MAX_CHAR_LIMIT)) == (1, HARD_MAX_CHAR_LIMIT)


def test_min_greater_than_max_is_rejected():
    assert _clamp_char_limits("700", "600") is None


def test_non_numeric_input_is_rejected():
    assert _clamp_char_limits("abc", "700") is None


def test_zero_or_negative_is_rejected():
    assert _clamp_char_limits("0", "700") is None
    assert _clamp_char_limits("600", "-1") is None
