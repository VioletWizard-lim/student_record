import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 세션 쿠키 서명 비밀키로 쓰면 안 되는 값(예시·기본값). 이 값이면 누구나 쿠키를 만들 수 있다.
PLACEHOLDER_SECRETS = {"", "dev-secret-change-me", "change-me-to-a-random-long-string"}
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"

    session_secret: str = "dev-secret-change-me"
    debug: bool = True

    monthly_limit: int = 200
    rolling_window_days: int = 30

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_production(self) -> bool:
        """운영 서버인지. Fly.io 위에서 돌면(FLY_APP_NAME이 있으면) DEBUG 값과 상관없이 운영으로 본다."""
        return not self.debug or bool(os.environ.get("FLY_APP_NAME"))

    @model_validator(mode="after")
    def _require_session_secret_in_production(self):
        # 운영에서 기본값·예시 비밀키로 켜지면 세션 쿠키를 위조할 수 있으므로 아예 켜지지 않게 한다
        if self.is_production:
            secret = self.session_secret.strip()
            if secret in PLACEHOLDER_SECRETS or len(secret) < MIN_SECRET_LENGTH:
                raise ValueError(
                    f"운영 환경에서는 SESSION_SECRET을 {MIN_SECRET_LENGTH}자 이상의 임의 문자열로 설정해야 합니다 "
                    "(예: python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
                )
        return self


settings = Settings()
