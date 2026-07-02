"""NEIS 생기부 글자수 계산.

엑셀 수식(나이스 공식 계산식)을 그대로 파이썬으로 옮긴 것:
    =2*LENB(cell) - LEN(SUBSTITUTE(cell, CHAR(10), ""))

LENB는 바이트수(ASCII 1바이트, 그 외 문자 2바이트)를 기준으로 하고,
LEN은 개행을 제거한 순수 글자수를 기준으로 한다.
"""


def _lenb(text: str) -> int:
    return sum(1 if ord(ch) < 128 else 2 for ch in text)


def neis_char_count(text: str) -> int:
    stripped = text.replace("\n", "")
    return 2 * _lenb(text) - len(stripped)
