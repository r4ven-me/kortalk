"""kortalk configuration: YAML in ~/.config/kortalk/config.yaml."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "kortalk"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

GENERAL_DEFAULTS = {
    "language": "en",           # en | ru — interface language
    "theme": "system",          # system | nord-dark | nord-light
    "font_family": "",          # "" = system font
    "font_size": 0,             # 0 = system size
    "popup_width": 560,
    "popup_max_height": 600,
    "timeout": 180,             # seconds, for CLI/HTTP requests
    "max_tokens": 64000,        # response token limit (Anthropic API)
    "active_provider": "claude-cli",
    "active_prompt": "Explain",
}

HOTKEY_DEFAULTS = {
    "window": "Ctrl+Alt+W",
}

# Prompts are user data: created once on first run and then live in the
# config file, so the defaults are in the default language (en). "Explain"
# is the default active prompt, so it keeps the classic Ctrl+Alt+C binding
# that used to be a separate, prompt-independent "popup" hotkey.
DEFAULT_PROMPTS = [
    {"name": "Explain", "text": "Briefly explain or comment on the following text:",
     "hotkey": "Ctrl+Alt+C"},
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
    hotkey: str = ""  # global hotkey that opens the popup with this prompt


@dataclass
class Provider:
    id: str
    name: str
    type: str            # "claude-cli" | "anthropic" | "openai"
    model: str = ""
    api_key: str = ""
    base_url: str = ""   # openai-compatible only
    extra_args: list[str] = field(default_factory=list)  # claude-cli only

    def needs_api_key(self) -> bool:
        # Local OpenAI-compatible servers (Ollama, LM Studio) need no key.
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

# Types of the stock providers — used to recognize configs corrupted by the
# settings-dialog bug in versions <= 0.3.0 (data of the adjacent provider
# was written into the previous list item).
_DEFAULT_PROVIDER_TYPES = {p.id: p.type for p in DEFAULT_PROVIDERS}


class Config:
    """YAML config with sections general / hotkeys / prompts / providers.
    Written to disk on every change (write-through)."""

    def __init__(self) -> None:
        self._data: dict = {}
        if CONFIG_FILE.exists():
            try:
                self._data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                # a broken file must not block startup — fall back to defaults
                self._data = {}
        repaired = self._repair_scrambled_providers()
        migrated = self._migrate_popup_hotkey()
        changed = self._ensure_defaults()
        if repaired or migrated or changed or not CONFIG_FILE.exists():
            self.save()

    def _repair_scrambled_providers(self) -> bool:
        """Resets providers to defaults when the config was scrambled by the
        settings bug of versions <= 0.3.0. The telltale sign is a stock id
        with a foreign type (provider ids are not editable in the dialog,
        types are, but the bug moved the neighbour's type over). Such
        records cannot be restored reliably."""
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

    def _migrate_popup_hotkey(self) -> bool:
        """<= 0.4.x had a standalone "popup" hotkey, independent of any
        prompt. Hotkey assignment now lives entirely on prompts (Settings →
        Prompts), so a configured value is carried over to the active
        prompt if that prompt doesn't already have its own hotkey."""
        hotkeys = self._data.get("hotkeys")
        if not isinstance(hotkeys, dict) or "popup" not in hotkeys:
            return False
        value = str(hotkeys.pop("popup") or "")
        if value:
            active_name = self._data.get("general", {}).get("active_prompt")
            for p in self._data.get("prompts") or []:
                if isinstance(p, dict) and p.get("name") == active_name and not p.get("hotkey"):
                    p["hotkey"] = value
                    break
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

    # -- general settings -----------------------------------------------------

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

    # -- hotkeys --------------------------------------------------------------

    def hotkey(self, action: str) -> str:
        return str(self._data["hotkeys"].get(action, "") or "")

    def set_hotkey(self, action: str, sequence: str) -> None:
        self._data["hotkeys"][action] = sequence
        self.save()

    # -- prompts --------------------------------------------------------------

    def prompts(self) -> list[Prompt]:
        return [Prompt(name=str(p.get("name", "")), text=str(p.get("text", "")),
                       hotkey=str(p.get("hotkey", "") or ""))
                for p in self._data.get("prompts", [])]

    def set_prompts(self, prompts: list[Prompt]) -> None:
        self._data["prompts"] = [
            {"name": p.name, "text": p.text, "hotkey": p.hotkey} for p in prompts
        ]
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

    # -- providers ------------------------------------------------------------

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

    # -- persistence ----------------------------------------------------------

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            yaml.safe_dump(self._data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        try:
            os.chmod(CONFIG_FILE, 0o600)  # the file may contain API keys
        except OSError:
            pass

    def sync(self) -> None:  # backward-compatible alias
        self.save()

    def file_path(self) -> str:
        return str(CONFIG_FILE)
