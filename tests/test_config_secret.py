import pytest

from app.config import Settings

BASE = {"supabase_url": "x", "supabase_anon_key": "x", "supabase_service_role_key": "x"}
STRONG = "s" * 48


def make(monkeypatch, fly=False, **kwargs):
    if fly:
        monkeypatch.setenv("FLY_APP_NAME", "saenggibu-doumi")
    else:
        monkeypatch.delenv("FLY_APP_NAME", raising=False)
    return Settings(_env_file=None, **BASE, **kwargs)


def test_debug_locally_allows_default_secret(monkeypatch):
    settings = make(monkeypatch, debug=True)
    assert not settings.is_production


@pytest.mark.parametrize("secret", ["dev-secret-change-me", "change-me-to-a-random-long-string", "short"])
def test_production_rejects_weak_secret(monkeypatch, secret):
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        make(monkeypatch, debug=False, session_secret=secret)


def test_fly_counts_as_production_even_with_debug(monkeypatch):
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        make(monkeypatch, fly=True, debug=True)
    settings = make(monkeypatch, fly=True, debug=True, session_secret=STRONG)
    assert settings.is_production


def test_production_accepts_strong_secret(monkeypatch):
    assert make(monkeypatch, debug=False, session_secret=STRONG).is_production
