import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.deps import RedirectException, get_current_user

PROFILE_ROW = {"id": "user-1", "status": "approved", "role": "user", "subjects": []}


class FakeRequest:
    def __init__(self, session: dict):
        self.session = session


def _ok_response():
    return SimpleNamespace(data=PROFILE_ROW)


def run(coro):
    return asyncio.run(coro)


def test_valid_token_does_not_trigger_refresh():
    session = {"access_token": "valid-token", "refresh_token": "r1", "user_id": "user-1"}
    with patch("app.deps._fetch_profile", return_value=_ok_response()) as fetch, patch(
        "app.deps.get_anon_client"
    ) as anon_client:
        current = run(get_current_user(FakeRequest(session)))
    assert current["profile"] == PROFILE_ROW
    assert current["access_token"] == "valid-token"
    fetch.assert_called_once_with("valid-token", "user-1")
    anon_client.assert_not_called()


def test_expired_token_refreshes_and_retries():
    session = {"access_token": "expired-token", "refresh_token": "r1", "user_id": "user-1"}
    refreshed = SimpleNamespace(
        session=SimpleNamespace(access_token="new-token", refresh_token="r2")
    )
    anon = SimpleNamespace(auth=SimpleNamespace(refresh_session=lambda token: refreshed))

    with patch(
        "app.deps._fetch_profile", side_effect=[Exception("jwt expired"), _ok_response()]
    ) as fetch, patch("app.deps.get_anon_client", return_value=anon):
        current = run(get_current_user(FakeRequest(session)))

    assert current["profile"] == PROFILE_ROW
    assert current["access_token"] == "new-token"
    assert session["access_token"] == "new-token"
    assert session["refresh_token"] == "r2"
    assert fetch.call_count == 2
    fetch.assert_any_call("expired-token", "user-1")
    fetch.assert_any_call("new-token", "user-1")


def test_expired_token_with_failed_refresh_logs_out():
    session = {"access_token": "expired-token", "refresh_token": "bad-refresh", "user_id": "user-1"}
    anon = SimpleNamespace(
        auth=SimpleNamespace(
            refresh_session=lambda token: (_ for _ in ()).throw(Exception("invalid refresh token"))
        )
    )

    with patch("app.deps._fetch_profile", side_effect=Exception("jwt expired")), patch(
        "app.deps.get_anon_client", return_value=anon
    ):
        with pytest.raises(RedirectException) as exc_info:
            run(get_current_user(FakeRequest(session)))

    assert exc_info.value.url == "/login"
    assert session == {}


def test_expired_token_without_refresh_token_logs_out_immediately():
    session = {"access_token": "expired-token", "refresh_token": "", "user_id": "user-1"}
    with patch("app.deps._fetch_profile", side_effect=Exception("jwt expired")), patch(
        "app.deps.get_anon_client"
    ) as anon_client:
        with pytest.raises(RedirectException) as exc_info:
            run(get_current_user(FakeRequest(session)))

    assert exc_info.value.url == "/login"
    anon_client.assert_not_called()


def test_missing_session_data_redirects_without_any_supabase_call():
    with patch("app.deps._fetch_profile") as fetch:
        with pytest.raises(RedirectException) as exc_info:
            run(get_current_user(FakeRequest({})))

    assert exc_info.value.url == "/login"
    fetch.assert_not_called()
