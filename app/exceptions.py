class RedirectException(Exception):
    """세션이 없거나 승인 대기 상태일 때 지정된 경로로 리다이렉트하기 위한 예외."""

    def __init__(self, url: str):
        self.url = url
