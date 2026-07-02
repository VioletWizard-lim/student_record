from app.generation import _build_length_retry_prompt


def test_retry_prompt_asks_to_shorten_when_over_max():
    prompt = _build_length_retry_prompt(char_count=2300, min_char_limit=1000, max_char_limit=2000)
    assert "2300바이트" in prompt
    assert "1000~2000바이트" in prompt
    assert "줄여서" in prompt


def test_retry_prompt_asks_to_lengthen_when_under_min():
    prompt = _build_length_retry_prompt(char_count=400, min_char_limit=600, max_char_limit=700)
    assert "400바이트" in prompt
    assert "늘려서" in prompt
