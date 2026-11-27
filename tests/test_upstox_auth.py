"""Regression tests for Upstox OAuth login sidebar flow (prevents UDAPI1013 blank client_id)."""
from pathlib import Path

import pytest


def test_build_upstox_login_url_rejects_empty_api_key():
    from quantdesk.ui.components import build_upstox_login_url

    with pytest.raises(ValueError, match="API Key is required"):
        build_upstox_login_url("", "http://localhost:8501")

    with pytest.raises(ValueError, match="API Key is required"):
        build_upstox_login_url("   ", "http://localhost:8501")

    url = build_upstox_login_url("  key123  ", " http://localhost:8501 ")
    assert "client_id=key123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8501" in url


def test_extract_upstox_auth_code_handles_raw_and_full_redirect_url():
    from quantdesk.ui.components import extract_upstox_auth_code

    assert extract_upstox_auth_code("  abc123  ") == "abc123"
    assert extract_upstox_auth_code("http://localhost:8501/?code=xyz789&foo=bar") == "xyz789"
    assert extract_upstox_auth_code("?code=q99") == "q99"
    assert extract_upstox_auth_code("") == ""


def test_exchange_upstox_token_validates_before_http_and_parses_response():
    from quantdesk.ui.components import exchange_upstox_token

    called = []

    def fail_if_called(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("HTTP POST must not fire when fields are missing")

    ok, msg = exchange_upstox_token("", "sec", "http://localhost:8501", "code1", post_fn=fail_if_called)
    assert not ok and "API Key is required" in msg
    assert not called

    class _FakeResp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    captured = {}

    def ok_post(url, headers=None, data=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        return _FakeResp(200, {"access_token": "tok_abc"})

    ok, token = exchange_upstox_token(
        " my_key ",
        " my_secret ",
        " http://localhost:8501 ",
        "http://localhost:8501/?code=auth_999",
        post_fn=ok_post,
    )
    assert ok is True
    assert token == "tok_abc"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["data"] == {
        "code": "auth_999",
        "client_id": "my_key",
        "client_secret": "my_secret",
        "redirect_uri": "http://localhost:8501",
        "grant_type": "authorization_code",
    }

    def err_post(url, headers=None, data=None, timeout=None):
        return _FakeResp(
            400,
            {"status": "error", "errors": [{"errorCode": "UDAPI1013", "message": "API Key is required"}]},
        )

    ok, err = exchange_upstox_token("k", "s", "http://localhost:8501", "c", post_fn=err_post)
    assert not ok
    assert "UDAPI1013" in err and "API Key is required" in err


def test_upstox_sidebar_apptest_blocks_blank_key_and_remembers_api_key(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from quantdesk import config as C
    from quantdesk.core import db as db_mod, settings_repo

    db_path = tmp_path / "test_auth.db"
    monkeypatch.setattr(C, "DB_FILE", str(db_path))
    monkeypatch.setattr(db_mod, "DB_FILE", str(db_path))
    monkeypatch.setattr(db_mod, "_initialised", False)
    settings_repo._cache = None
    monkeypatch.setattr("quantdesk.ui.components._load_dotenv_if_present", lambda: None)
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
    monkeypatch.delenv("UPSTOX_API_SECRET", raising=False)

    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_main.py"), default_timeout=20)
    at.run()

    # Clicking Login (Upstox) with empty API Key must error clearly instead of emitting client_id=
    login_btn = [b for b in at.sidebar.button if b.label == "Login (Upstox)"][0]
    login_btn.click().run()
    assert any("API Key is required" in e.value for e in at.sidebar.error)

    # Entering API Key and clicking Login (Upstox) saves API Key & generates valid dialog URL
    at.sidebar.text_input(key="upstox_api_key_input").input("my_upstox_key").run()
    login_btn = [b for b in at.sidebar.button if b.label == "Login (Upstox)"][0]
    login_btn.click().run()
    assert settings_repo.get("upstox_api_key") == "my_upstox_key"
    assert any("client_id=my_upstox_key" in m.value for m in at.sidebar.markdown)

    # Simulating a new session after OAuth redirect (?code=auth_42): API Key stays populated from DB
    at2 = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app_main.py"), default_timeout=20)
    at2.query_params["code"] = "auth_42"
    at2.run()
    assert at2.sidebar.text_input(key="upstox_api_key_input").value == "my_upstox_key"
    assert at2.sidebar.text_input(key="upstox_auth_code_input").value == "auth_42"


def test_save_local_dotenv_writes_clean_env_file(tmp_path, monkeypatch):
    from quantdesk.ui.components import save_local_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text('OTHER_KEY="keep"\nUPSTOX_API_KEY="old"\n', encoding="utf-8")
    monkeypatch.delenv("UPSTOX_API_KEY", raising=False)
    monkeypatch.delenv("UPSTOX_API_SECRET", raising=False)
    monkeypatch.delenv("UPSTOX_REDIRECT_URI", raising=False)

    out_path = save_local_dotenv(" key_new ", " sec_new ", " http://localhost:8501 ", env_path=env_file)
    assert out_path == str(env_file)
    txt = env_file.read_text(encoding="utf-8")
    assert 'OTHER_KEY="keep"' in txt
    assert 'UPSTOX_API_KEY="key_new"' in txt
    assert 'UPSTOX_API_SECRET="sec_new"' in txt
    assert 'UPSTOX_REDIRECT_URI="http://localhost:8501"' in txt
    assert txt.count("UPSTOX_API_KEY=") == 1
