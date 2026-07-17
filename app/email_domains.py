"""한국 시도교육청 이메일 도메인 화이트리스트.

가입은 아래 목록에 있는 교육청 도메인이거나, 그 하위 학교 서브도메인
(예: somehigh.ice.go.kr)으로만 허용한다.

주의: 아래 목록은 알려진 도메인을 정리한 것으로, 실제 교육청 도메인과
다르면 이 목록만 수정하면 된다.
"""

EDUCATION_OFFICE_DOMAINS = {
    "sen.go.kr",  # 서울특별시교육청
    "pen.go.kr",  # 부산광역시교육청
    "dge.go.kr",  # 대구광역시교육청
    "ice.go.kr",  # 인천광역시교육청
    "gen.go.kr",  # 광주광역시교육청
    "dje.go.kr",  # 대전광역시교육청
    "use.go.kr",  # 울산광역시교육청
    "sje.go.kr",  # 세종특별자치시교육청
    "goe.go.kr",  # 경기도교육청
    "gwe.go.kr",  # 강원특별자치도교육청
    "kwe.go.kr",  # 강원도교육청 (구 도메인)
    "cbe.go.kr",  # 충청북도교육청
    "cne.go.kr",  # 충청남도교육청
    "jbe.go.kr",  # 전북특별자치도교육청
    "jne.go.kr",  # 전라남도교육청
    "gbe.kr",  # 경상북도교육청 (다른 교육청과 달리 .go.kr이 아니라 .kr)
    "gne.go.kr",  # 경상남도교육청
    "jje.go.kr",  # 제주특별자치도교육청
}


def is_allowed_education_email(email: str) -> bool:
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    return any(
        domain == allowed or domain.endswith("." + allowed)
        for allowed in EDUCATION_OFFICE_DOMAINS
    )
