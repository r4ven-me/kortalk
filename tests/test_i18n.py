"""Localization tests: en by default, ru translations, template integrity."""

import re

import pytest

from kortalk.i18n import RU, set_language, tr


@pytest.fixture(autouse=True)
def _reset_language():
    yield
    set_language("en")


def test_english_is_default_and_passthrough():
    set_language("en")
    assert tr("Settings") == "Settings"


def test_russian_translation():
    set_language("ru")
    assert tr("Settings") == "Настройки"
    assert tr("Quit") == "Выход"
    # an unknown key is returned as is
    assert tr("no such key") == "no such key"


def test_unknown_language_falls_back_to_english():
    set_language("de")
    assert tr("Settings") == "Settings"


def test_ru_templates_keep_placeholders():
    # {fields} in a translation must match the English key
    for en, ru in RU.items():
        assert set(re.findall(r"{\w+}", en)) == set(re.findall(r"{\w+}", ru)), en
