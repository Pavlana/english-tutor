import importlib
from unittest.mock import patch


def test_model_tier_constants():
    from src.config import settings

    assert settings.model_opus == "claude-opus-4-7"
    assert settings.model_sonnet == "claude-sonnet-4-6"
    assert settings.model_haiku == "claude-haiku-4-5-20251001"


def test_settings_loads_api_key_from_env():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-abc"}):
        import src.config as config_module

        fresh = importlib.reload(config_module)
        assert fresh.settings.ANTHROPIC_API_KEY == "test-key-abc"
