"""Общие фикстуры: изолированный конфиг и headless-Qt."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import kortalk.config as config_mod
from kortalk.config import Config


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """Перенаправляет конфиг kortalk во временный каталог."""
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.yaml")
    return tmp_path


@pytest.fixture
def config(config_dir):
    return Config()


@pytest.fixture(autouse=True)
def _isolate_autostart(tmp_path, monkeypatch):
    """Диалог настроек не должен трогать реальный ~/.config/autostart."""
    import kortalk.settings_dialog as settings_dialog

    monkeypatch.setattr(
        settings_dialog, "AUTOSTART_FILE", tmp_path / "autostart" / "kortalk.desktop"
    )
