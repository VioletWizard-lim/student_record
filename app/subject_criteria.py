"""과목별 성취기준 코드/설명 목록.

각 과목은 (코드, 설명) 튜플의 리스트를 값으로 가진다.
예: "12정01-01" 처럼 2022 개정 교육과정 성취기준 코드 형식을 그대로 사용한다.

실제 데이터로 교체해서 사용한다. 여기 있는 값은 자리표시자(placeholder)이며,
실제 성취기준 코드/설명으로 바꿔야 한다.
"""

SUBJECT_CRITERIA: dict[str, list[tuple[str, str]]] = {
    "정보": [
        ("12정01-01", "성취기준 설명을 입력하세요"),
        ("12정01-02", "성취기준 설명을 입력하세요"),
    ],
}


def get_subjects() -> list[str]:
    return list(SUBJECT_CRITERIA.keys())


def get_criteria(subject: str) -> list[tuple[str, str]]:
    return SUBJECT_CRITERIA.get(subject, [])


def criterion_label(subject: str, code: str) -> str:
    for candidate_code, description in get_criteria(subject):
        if candidate_code == code:
            return f"{code} ({description})" if description else code
    return code
