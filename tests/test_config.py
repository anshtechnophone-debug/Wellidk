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
