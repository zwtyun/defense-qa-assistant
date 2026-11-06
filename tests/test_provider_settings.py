import json
import os

import pytest

from provider_settings import ProviderSettings, normalize_base_url


def test_default_settings_read_does_not_write_files(tmp_path):
    settings = ProviderSettings(tmp_path)
    assert settings.public()["provider"] == "local"
    assert settings.snapshot()["api_key"] == ""
    assert list(tmp_path.iterdir()) == []


def test_model_selection_is_left_to_user(tmp_path):
    settings = ProviderSettings(tmp_path)
    assert settings.public()["model"] == ""
    result = settings.update({"model": "", "remember_key": False})
    assert result["model"] == ""


def test_malformed_saved_config_keeps_local_app_available(tmp_path):
    (tmp_path / "generation-settings.json").write_text("[]", encoding="utf-8")
    settings = ProviderSettings(tmp_path)
    assert settings.public()["provider"] == "local"
    assert settings.public()["credential_warning"]


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_saved_key_is_encrypted_and_reloads_for_current_windows_user(tmp_path):
    settings = ProviderSettings(tmp_path)
    secret = "test-local-only-secret-not-a-real-api-key"
    result = settings.update({"api_key": secret, "provider": "api"})
    assert result["has_api_key"] and result["key_storage"] == "windows"
    assert "api_key" not in result and secret not in json.dumps(result)
    saved = settings.path.read_text(encoding="utf-8")
    assert secret not in saved
    assert ProviderSettings(tmp_path).snapshot()["api_key"] == secret
    assert settings.update({"model": "new-model", "api_key": ""})["has_api_key"]
    assert ProviderSettings(tmp_path).snapshot()["api_key"] == secret


def test_session_key_not_saved_and_clear_removes_it(tmp_path):
    settings = ProviderSettings(tmp_path)
    settings.update({"api_key": "session-only-example", "remember_key": False})
    assert settings.public()["key_storage"] == "session"
    assert settings.snapshot()["api_key"] == "session-only-example"
    assert not ProviderSettings(tmp_path).public()["has_api_key"]
    assert "session-only-example" not in settings.path.read_text(encoding="utf-8")
    assert not settings.update({"clear_api_key": True})["has_api_key"]


def test_preview_does_not_persist_and_new_destination_needs_its_own_key(tmp_path):
    settings = ProviderSettings(tmp_path)
    settings.update({"api_key": "service-one-example", "remember_key": False})
    before = settings.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="重新填写"):
        settings.preview({"base_url": "https://example.com"})
    preview = settings.preview({"base_url": "https://example.com", "api_key": "service-two-example"})
    assert preview["base_url"] == "https://example.com/v1"
    assert preview["api_key"] == "service-two-example"
    assert settings.snapshot()["api_key"] == "service-one-example"
    assert settings.path.read_text(encoding="utf-8") == before
    assert settings.preview({"base_url": "https://example.com", "clear_api_key": True})["api_key"] == ""


@pytest.mark.parametrize("address", ["http://example.com", "https://user:password@example.com", "https://example.com?key=secret", "https://example.com#token", "https://example.com/v1/responses", "https://example.com:bad"])
def test_unsafe_or_non_base_urls_rejected(address):
    with pytest.raises(ValueError):
        normalize_base_url(address)


def test_urls_normalized_and_local_test_server_supported():
    assert normalize_base_url(" https://API.Example.com/ ") == "https://api.example.com/v1"
    assert normalize_base_url("https://example.com/openai/v1/") == "https://example.com/openai/v1"
    assert normalize_base_url("http://127.0.0.1:8788/v1") == "http://127.0.0.1:8788/v1"


def test_failed_save_preserves_previous_settings(tmp_path, monkeypatch):
    settings = ProviderSettings(tmp_path)
    settings.update({"model": "original", "remember_key": False})
    before = settings.path.read_text(encoding="utf-8")
    def failed_encrypt(data, decrypt=False):
        raise ValueError("模拟系统加密失败")
    monkeypatch.setattr("provider_settings._protect", failed_encrypt)
    with pytest.raises(ValueError):
        settings.update({"api_key": "example", "remember_key": True, "model": "unsaved"})
    assert settings.public()["model"] == "original"
    assert settings.path.read_text(encoding="utf-8") == before


def test_wrong_windows_identity_keeps_preferences_and_requests_new_key(tmp_path, monkeypatch):
    (tmp_path / "generation-settings.json").write_text(json.dumps({"provider": "api", "model": "chosen", "encrypted_api_key": "YWJj"}), encoding="utf-8")
    def failed_decrypt(data, decrypt=False):
        raise ValueError("模拟其他Windows用户")
    monkeypatch.setattr("provider_settings._protect", failed_decrypt)
    settings = ProviderSettings(tmp_path)
    assert settings.public()["provider"] == "api"
    assert settings.public()["model"] == "chosen"
    assert not settings.public()["has_api_key"]
    assert settings.public()["credential_warning"]


@pytest.mark.parametrize("patch", [{"remember_key": "false"}, {"use_system_proxy": "false"}, {"timeout_seconds": True}, {"timeout_seconds": 999}, {"api_key": "line1\nline2"}, {"api_key": 123}, {"clear_api_key": "yes"}, {"reasoning_effort": "bogus"}])
def test_invalid_configuration_never_writes(tmp_path, patch):
    settings = ProviderSettings(tmp_path)
    with pytest.raises(ValueError):
        settings.update(patch)
    assert list(tmp_path.iterdir()) == []
