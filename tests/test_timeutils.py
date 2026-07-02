from app.timeutils import to_kst_display


def test_converts_utc_offset_string_to_kst():
    # UTC 10:00 -> KST(UTC+9) 19:00
    assert to_kst_display("2026-07-01T10:00:00+00:00") == "2026-07-01 19:00"


def test_converts_z_suffix_to_kst():
    assert to_kst_display("2026-07-01T10:00:00Z") == "2026-07-01 19:00"


def test_crosses_midnight_into_next_day():
    # UTC 20:00 -> KST 다음날 05:00
    assert to_kst_display("2026-07-01T20:00:00+00:00") == "2026-07-02 05:00"


def test_naive_string_without_offset_assumed_utc():
    assert to_kst_display("2026-07-01T10:00:00") == "2026-07-01 19:00"


def test_empty_or_none_returns_empty_string():
    assert to_kst_display("") == ""
    assert to_kst_display(None) == ""


def test_unparseable_string_falls_back_to_raw_slice():
    assert to_kst_display("garbage") == "garbage"
