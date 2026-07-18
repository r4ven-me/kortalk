"""Конфигурация kortalk: YAML в ~/.config/kortalk/config.yaml."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "kortalk"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

GENERAL_DEFAULTS = {
    "language": "en",           # en | ru — язык интерфейса
    "theme": "system",          # system | nord-dark | nord-light
    "font_family": "",          # "" = системный шрифт
    "font_size": 0,             # 0 = системный размер
    "popup_width": 560,
    "popup_max_height": 600,
    "timeout": 180,             # сек, для CLI/HTTP-запросов
    "max_tokens": 64000,        # лимит токенов ответа (Anthropic API)
    "active_provider": "claude-cli",
    "active_prompt": "Explain",
}

HOTKEY_DEFAULTS = {
    "popup": "Ctrl+Alt+C",
    "window": "Ctrl+Alt+W",
}

# Промпты — пользовательские данные: создаются один раз при первом запуске
# и дальше живут в конфиге, поэтому дефолты — на языке по умолчанию (en).
DEFAULT_PROMPTS = [
    {"name": "Explain", "text": "Briefly explain or comment on the following text:"},
    {"name": "Translate",
     "text": "Translate the following text to English (if it is already in English — "
             "to Russian), reply with the translation only:"},
    {"name": "Fix",
     "text": "Fix the grammar and style of the following text, "
             "reply with the corrected text only:"},
]


@dataclass
class Prompt:
    name: str
    text: str


@dataclass
class Provider:
    id: str
    name: str
    type: str            # "claude-cli" | "anthropic" | "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""   # только для openai-совместимых
    extra_args: list[str] = field(default_factory=list)  # только для claude-cli

    def needs_api_key(self) -> bool:
        # Локальные OpenAI-совместимые серверы (Ollama, LM Studio) ключ не требуют.
        if self.type == "anthropic":
            return True
        if self.type == "openai":
            return "localhost" not in self.base_url and "127.0.0.1" not in self.base_url
        return False


DEFAULT_PROVIDERS = [
    Provider(id="claude-cli", name="Claude Code CLI", type="claude-cli"),
    Provider(id="anthropic", name="Anthropic API", type="anthropic", model="claude-opus-4-8"),
    Provider(id="openai", name="OpenAI API", type="openai",
             model="gpt-4o", base_url="https://api.openai.com/v1"),
    Provider(id="ollama", name="Ollama (local)", type="openai",
             base_url="http://localhost:11434/v1"),
]

# Типы стандартных провайдеров — для распознавания конфигов, испорченных
# багом диалога настроек версий <= 0.3.0 (данные соседнего провайдера
# записывались в предыдущий элемент списка).
_DEFAULT_PROVIDER_TYPES = {p.id: p.type for p in DEFAULT_PROVIDERS}


class Config:
    """YAML-конфиг: секции general / hotkeys / prompts / providers.
    Записывается на диск при каждом изменении (write-through)."""

    def __init__(self) -> None:
        self._data: dict = {}
        if CONFIG_FILE.exists():
            try:
                self._data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                # битый файл не должен блокировать запуск — работаем с дефолтами
                self._data = {}
        repaired = self._repair_scrambled_providers()
        changed = self._ensure_defaults()
        if repaired or changed or not CONFIG_FILE.exists():
            self.save()

    def _repair_scrambled_providers(self) -> bool:
        """Сбрасывает провайдеров к дефолтам, если конфиг перемешан багом
        настроек версий <= 0.3.0. Признак порчи — стандартный id с чужим
        типом (id провайдера в диалоге не редактируется, тип — да, но баг
        переносил тип соседнего провайдера). Достоверно восстановить такие
        записи нельзя."""
        providers = self._data.get("providers")
        if not isinstance(providers, list):
            return False
        scrambled = any(
            isinstance(d, dict)
            and d.get("id") in _DEFAULT_PROVIDER_TYPES
            and d.get("type") != _DEFAULT_PROVIDER_TYPES[d.get("id")]
            for d in providers
        )
        if not scrambled:
            return False
        self._data["providers"] = [self._provider_to_dict(p) for p in DEFAULT_PROVIDERS]
        print(
            "kortalk: provider configuration was corrupted by a previous version — "
            "defaults restored (set API keys again in Settings → Providers).",
            file=sys.stderr,
        )
        return True

    def _ensure_defaults(self) -> bool:
        changed = False
        general = self._data.setdefault("general", {})
        for key, value in GENERAL_DEFAULTS.items():
            if key not in general:
                general[key] = value
                changed = True
        hotkeys = self._data.setdefault("hotkeys", {})
        for key, value in HOTKEY_DEFAULTS.items():
            if key not in hotkeys:
                hotkeys[key] = value
                changed = True
        if not self._data.get("prompts"):
            self._data["prompts"] = [dict(p) for p in DEFAULT_PROMPTS]
            changed = True
        if not self._data.get("providers"):
            self._data["providers"] = [self._provider_to_dict(p) for p in DEFAULT_PROVIDERS]
            changed = True
        return changed

    # -- общие настройки ------------------------------------------------------

    def get(self, key: str):
        default = GENERAL_DEFAULTS[key]
        value = self._data["general"].get(key, default)
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        return value

    def set(self, key: str, value) -> None:
        self._data["general"][key] = value
        self.save()

    # -- хоткеи ----------------------------------------------------------------

    def hotkey(self, action: str) -> str:
        return str(self._data["hotkeys"].get(action, "") or "")

    def set_hotkey(self, action: str, sequence: str) -> None:
        self._data["hotkeys"][action] = sequence
        self.save()

    # -- промпты -----------------------------------------------------------------

    def prompts(self) -> list[Prompt]:
        return [Prompt(name=str(p.get("name", "")), text=str(p.get("text", "")))
                for p in self._data.get("prompts", [])]

    def set_prompts(self, prompts: list[Prompt]) -> None:
        self._data["prompts"] = [{"name": p.name, "text": p.text} for p in prompts]
        self.save()

    def prompt_by_name(self, name: str) -> Prompt | None:
        for p in self.prompts():
            if p.name == name:
                return p
        return None

    def active_prompt(self) -> Prompt:
        p = self.prompt_by_name(str(self.get("active_prompt")))
        if p is None:
            prompts = self.prompts()
            p = prompts[0] if prompts else Prompt(**DEFAULT_PROMPTS[0])
        return p

    # -- провайдеры ---------------------------------------------------------------

    @staticmethod
    def _provider_to_dict(p: Provider) -> dict:
        return {
            "id": p.id, "name": p.name, "type": p.type, "model": p.model,
            "api_key": p.api_key, "base_url": p.base_url, "extra_args": p.extra_args,
        }

    def providers(self) -> list[Provider]:
        result = []
        for d in self._data.get("providers", []):
            result.append(Provider(
                id=str(d.get("id", "")),
                name=str(d.get("name", d.get("id", ""))),
                type=str(d.get("type", "openai")),
                model=str(d.get("model", "") or ""),
                api_key=str(d.get("api_key", "") or ""),
                base_url=str(d.get("base_url", "") or ""),
                extra_args=[str(a) for a in (d.get("extra_args") or [])],
            ))
        return result

    def provider(self, pid: str) -> Provider | None:
        for p in self.providers():
            if p.id == pid:
                return p
        return None

    def active_provider(self) -> Provider:
        p = self.provider(str(self.get("active_provider")))
        if p is None:
            providers = self.providers()
            p = providers[0] if providers else DEFAULT_PROVIDERS[0]
        return p

    def save_provider(self, p: Provider) -> None:
        items = self._data.setdefault("providers", [])
        for i, d in enumerate(items):
            if d.get("id") == p.id:
                items[i] = self._provider_to_dict(p)
                break
        else:
            items.append(self._provider_to_dict(p))
        self.save()

    def remove_provider(self, pid: str) -> None:
        self._data["providers"] = [
            d for d in self._data.get("providers", []) if d.get("id") != pid
        ]
        self.save()

    # -- запись ----------------------------------------------------------------------

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        try:
            os.chmod(CONFIG_FILE, 0o600)  # в файле могут лежать API-ключи
        except OSError:
            pass

    def sync(self) -> None:  # совместимость с прежним API
        self.save()

    def file_path(self) -> str:
        return str(CONFIG_FILE)
