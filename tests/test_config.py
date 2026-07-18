"""Тесты Config: дефолты, чтение/запись, починка испорченных конфигов."""

import yaml

from kortalk.config import DEFAULT_PROVIDERS, Config


def test_defaults_created_on_first_run(config, config_dir):
    assert (config_dir / "config.yaml").exists()
    assert [p.id for p in config.providers()] == [p.id for p in DEFAULT_PROVIDERS]
    assert config.active_provider().id == "claude-cli"
    assert config.hotkey("popup") == "Ctrl+Alt+C"


def test_set_get_roundtrip(config):
    config.set("popup_width", 700)
    assert config.get("popup_width") == 700
    # значения перечитываются из файла новым экземпляром
    assert Config().get("popup_width") == 700


def test_scrambled_providers_are_reset(config_dir):
    # Реальная форма порчи багом <= 0.3.0: id/name/type сдвинуты на один.
    scrambled = {
        "providers": [
            {"id": "claude-cli", "name": "Anthropic API", "type": "anthropic",
             "model": "claude-opus-4-8"},
            {"id": "anthropic", "name": "Anthropic API", "type": "claude-cli",
             "model": "claude-opus-4-8"},
            {"id": "openai", "name": "Ollama (локально)", "type": "openai",
             "base_url": "http://localhost:11434/v1"},
            {"id": "ollama", "name": "OpenAI API", "type": "openai",
             "model": "gpt-4o", "base_url": "https://api.openai.com/v1"},
        ],
        "general": {"active_provider": "ollama"},
    }
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(scrambled, allow_unicode=True), encoding="utf-8"
    )

    cfg = Config()

    got = {p.id: (p.name, p.type) for p in cfg.providers()}
    expected = {p.id: (p.name, p.type) for p in DEFAULT_PROVIDERS}
    assert got == expected
    # активный провайдер снова осмысленный: ollama — это действительно Ollama
    assert cfg.active_provider().base_url == "http://localhost:11434/v1"


def test_valid_config_is_not_touched(config_dir):
    valid = {
        "providers": [
            {"id": "anthropic", "name": "Мой Anthropic", "type": "anthropic",
             "model": "claude-opus-4-8", "api_key": "sk-test-123"},
            {"id": "my-local", "name": "LM Studio", "type": "openai",
             "base_url": "http://localhost:1234/v1", "model": "qwen3"},
        ],
    }
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(valid, allow_unicode=True), encoding="utf-8"
    )

    cfg = Config()

    assert cfg.provider("anthropic").api_key == "sk-test-123"
    assert cfg.provider("anthropic").name == "Мой Anthropic"
    assert cfg.provider("my-local").base_url == "http://localhost:1234/v1"


def test_broken_yaml_falls_back_to_defaults(config_dir):
    (config_dir / "config.yaml").write_text("providers: [broken", encoding="utf-8")
    cfg = Config()
    assert [p.id for p in cfg.providers()] == [p.id for p in DEFAULT_PROVIDERS]


def test_config_file_permissions_are_private(config, config_dir):
    # в файле могут храниться API-ключи
    mode = (config_dir / "config.yaml").stat().st_mode & 0o777
    assert mode == 0o600


def test_max_tokens_default(config):
    assert config.get("max_tokens") == 64000
