from functools import lru_cache

from supabase import Client, create_client

from app.config import settings


@lru_cache
def get_anon_client() -> Client:
    """비로그인 상태(가입/로그인 호출)에 사용하는 anon 키 클라이언트."""
    return create_client(settings.supabase_url, settings.supabase_anon_key)


@lru_cache
def get_service_client() -> Client:
    """RLS를 우회하는 관리자 전용 service-role 클라이언트. 관리자 라우트에서만 사용."""
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def get_user_client(access_token: str) -> Client:
    """로그인한 사용자의 access token으로 인증해 RLS가 적용되는 클라이언트를 생성."""
    client = create_client(settings.supabase_url, settings.supabase_anon_key)
    client.postgrest.auth(access_token)
    return client
