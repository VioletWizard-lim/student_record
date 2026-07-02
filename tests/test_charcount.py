from app.charcount import neis_byte_count


def test_pure_korean_text():
    # 2글자 한글, 개행 없음: LENB=4, LEN(개행제거)=2 -> 2*4-2=6
    assert neis_byte_count("안녕") == 6


def test_pure_english_text():
    # 2글자 영문, 개행 없음: LENB=2, LEN(개행제거)=2 -> 2*2-2=2
    assert neis_byte_count("ab") == 2


def test_empty_string():
    assert neis_byte_count("") == 0


def test_newline_is_excluded_from_len_but_present_in_lenb():
    # "a\nb": LENB=3(a,\n,b 각 1바이트), 개행제거 후 LEN=2("ab") -> 2*3-2=4
    assert neis_byte_count("a\nb") == 4
