import re

# 대한민국 주민등록번호 패턴: YYMMDD-[1-4]XXXXXX (구분자 없거나 공백도 허용)
RRN_PATTERN = re.compile(r"\d{6}[-\s]?[1-4]\d{6}")

SENSITIVE_INFO_NOTICE = (
    "입력 가능 범위: 이름, 학번 등 기본 식별 정보 + 학업/활동 관련 서술 자료만 입력해 주세요. "
    "주민등록번호는 자동으로 차단됩니다. 가족관계, 건강정보, 심리상담 내용 등 민감정보는 "
    "자동 탐지되지 않으니 직접 입력하지 않도록 주의해 주세요."
)


def contains_rrn(*texts: str) -> bool:
    return any(RRN_PATTERN.search(text) for text in texts if text)
