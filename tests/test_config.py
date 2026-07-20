"""Config tests: defaults, read/write, repair of corrupted configs."""

import yaml

from kortalk.config import DEFAULT_PROVIDERS, Config, Prompt


def test_defaults_created_on_first_run(config, config_dir):
    assert (config_dir / "config.yaml").exists()
    assert [p.id for p in config.providers()] == [p.id for p in DEFAULT_PROVIDERS]
    assert config.active_provider().id == "claude-cli"
    # the default active prompt carries the classic popup hotkey
    assert config.active_prompt().name == "Explain"
    assert config.active_prompt().hotkey == "Ctrl+Alt+C"
    assert config.hotkey("window") == "Ctrl+Alt+W"


def test_set_get_roundtrip(config):
    config.set("popup_width", 700)
    assert config.get("popup_width") == 700
    # values are re-read from the file by a fresh instance
    assert Config().get("popup_width") == 700


def test_prompt_hotkey_roundtrip(config):
    prompts = config.prompts()
    assert prompts[1].hotkey == ""  # "Translate" has no hotkey by default
    prompts[1].hotkey = "Ctrl+Alt+E"
    config.set_prompts(prompts)

    reloaded = Config().prompts()
    assert reloaded[1].hotkey == "Ctrl+Alt+E"
    assert reloaded[2].hotkey == ""


def test_prompts_without_hotkey_field_are_readable(config_dir):
    # configs written by older versions have no "hotkey" key
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump({"prompts": [{"name": "Old", "text": "Old text"}]}),
        encoding="utf-8",
    )
    prompts = Config().prompts()
    assert prompts[0] == Prompt(name="Old", text="Old text", hotkey="")


def test_popup_hotkey_migrates_to_active_prompt(config_dir):
    # <= 0.4.x config: a standalone "popup" hotkey, independent of prompts
    old = {
        "general": {"active_prompt": "Translate"},
        "hotkeys": {"popup": "Ctrl+Alt+P", "window": "Ctrl+Alt+W"},
        "prompts": [
            {"name": "Explain", "text": "Explain:"},
            {"name": "Translate", "text": "Translate:"},
        ],
    }
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(old, allow_unicode=True), encoding="utf-8"
    )

    cfg = Config()

    # moved onto the prompt that was active at the time, not left dangling
    assert cfg.prompt_by_name("Translate").hotkey == "Ctrl+Alt+P"
    assert cfg.prompt_by_name("Explain").hotkey == ""
    assert cfg.hotkey("window") == "Ctrl+Alt+W"
    # re-reading the now-migrated, saved config leaves it untouched
    reloaded = Config()
    assert reloaded.prompt_by_name("Translate").hotkey == "Ctrl+Alt+P"
    assert reloaded.prompt_by_name("Explain").hotkey == ""


def test_popup_hotkey_migration_does_not_override_existing_prompt_hotkey(config_dir):
    old = {
        "general": {"active_prompt": "Translate"},
        "hotkeys": {"popup": "Ctrl+Alt+P"},
        "prompts": [
            {"name": "Translate", "text": "Translate:", "hotkey": "Ctrl+Alt+T"},
        ],
    }
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(old, allow_unicode=True), encoding="utf-8"
    )

    cfg = Config()

    assert cfg.prompt_by_name("Translate").hotkey == "Ctrl+Alt+T"


def test_scrambled_providers_are_reset(config_dir):
    # The actual corruption shape of the <= 0.3.0 bug: id/name/type shifted by one.
    scrambled = {
        "providers": [
            {"id": "claude-cli", "name": "Anthropic API", "type": "anthropic",
             "model": "claude-opus-4-8"},
            {"id": "anthropic", "name": "Anthropic API", "type": "claude-cli",
             "model": "claude-opus-4-8"},
            {"id": "openai", "name": "Ollama (local)", "type": "openai",
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
    # the active provider makes sense again: ollama really is Ollama
    assert cfg.active_provider().base_url == "http://localhost:11434/v1"


def test_valid_config_is_not_touched(config_dir):
    valid = {
        "providers": [
            {"id": "anthropic", "name": "My Anthropic", "type": "anthropic",
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
    assert cfg.provider("anthropic").name == "My Anthropic"
    assert cfg.provider("my-local").base_url == "http://localhost:1234/v1"


def test_broken_yaml_falls_back_to_defaults(config_dir):
    (config_dir / "config.yaml").write_text("providers: [broken", encoding="utf-8")
    cfg = Config()
    assert [p.id for p in cfg.providers()] == [p.id for p in DEFAULT_PROVIDERS]


def test_config_file_permissions_are_private(config, config_dir):
    # the file may contain API keys
    mode = (config_dir / "config.yaml").stat().st_mode & 0o777
    assert mode == 0o600


def test_max_tokens_default(config):
    assert config.get("max_tokens") == 64000
