import pytest

from app.config import DEFAULT_DATABASE_URL, Settings


def test_settings_load_required_credentials_and_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings.from_env()
    assert settings.owner_id == 123456789
    assert settings.owner_admin_required is True
    assert settings.database_url == DEFAULT_DATABASE_URL


@pytest.mark.parametrize("owner_id", ["", "abc", "-10", "0"])
def test_settings_reject_invalid_owner_id(monkeypatch, tmp_path, owner_id):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", owner_id)
    with pytest.raises(ValueError, match="OWNER_ID"):
        Settings.from_env()


def test_settings_reject_non_boolean_owner_gate(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    monkeypatch.setenv("OWNER_ADMIN_REQUIRED", "sometimes")
    with pytest.raises(ValueError, match="OWNER_ADMIN_REQUIRED"):
        Settings.from_env()


def test_ai_is_disabled_and_emergency_stopped_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    settings = Settings.from_env()
    assert settings.ai_enabled is False
    assert settings.ai_global_emergency_stop is True


def test_ai_enable_requires_provider_key_model_and_kill_switch_disabled(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_API_KEY", "test-only")
    monkeypatch.setenv("AI_MODEL", "test-model")
    with pytest.raises(ValueError, match="AI_GLOBAL_EMERGENCY_STOP"):
        Settings.from_env()
    monkeypatch.setenv("AI_GLOBAL_EMERGENCY_STOP", "false")
    assert Settings.from_env().ai_enabled is True


def test_ai_base_url_rejects_lookalike_localhost_hostname(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    monkeypatch.setenv("AI_BASE_URL", "http://localhost.attacker.invalid/v1")
    with pytest.raises(ValueError, match="AI_BASE_URL"):
        Settings.from_env()


def test_ai_activity_window_and_threshold_defaults_are_configurable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:fake-token")
    monkeypatch.setenv("OWNER_ID", "123456789")
    monkeypatch.setenv("AI_ACTIVITY_THRESHOLD", "8")
    monkeypatch.setenv("AI_ACTIVITY_WINDOW_SECONDS", "90")
    settings = Settings.from_env()
    assert settings.ai_activity_threshold == 8
    assert settings.ai_activity_window_seconds == 90
