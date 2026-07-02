from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
