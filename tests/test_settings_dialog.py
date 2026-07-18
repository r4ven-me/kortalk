"""Регрессионные тесты диалога настроек.

Главный сценарий: листание списков провайдеров/промптов не должно
перемешивать записи (баг версий <= 0.3.0 записывал данные нового
элемента в предыдущий).
"""

from PySide6.QtCore import Qt

from kortalk.settings_dialog import SettingsDialog


def _provider_row(dlg, provider_id):
    for i in range(dlg.provider_list.count()):
        if dlg.provider_list.item(i).data(Qt.ItemDataRole.UserRole).id == provider_id:
            return i
    raise AssertionError(f"провайдер {provider_id} не найден в списке")


def test_browsing_providers_does_not_scramble(qtbot, config):
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)

    for _pass in range(2):  # туда и обратно, как листает пользователь
        for i in range(dlg.provider_list.count()):
            dlg.provider_list.setCurrentRow(i)
        for i in reversed(range(dlg.provider_list.count())):
            dlg.provider_list.setCurrentRow(i)
    dlg._save()

    got = {p.id: (p.name, p.type) for p in config.providers()}
    assert got["claude-cli"] == ("Claude Code CLI", "claude-cli")
    assert got["anthropic"] == ("Anthropic API", "anthropic")
    assert got["openai"] == ("OpenAI API", "openai")
    assert got["ollama"] == ("Ollama (local)", "openai")


def test_edit_persists_to_the_edited_provider_only(qtbot, config):
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)

    row = _provider_row(dlg, "anthropic")
    dlg.provider_list.setCurrentRow(row)
    dlg.p_model.setText("claude-opus-4-8")
    dlg.p_api_key.setText("sk-test-456")

    dlg.provider_list.setCurrentRow(_provider_row(dlg, "claude-cli"))
    dlg.provider_list.setCurrentRow(row)
    # после возврата форма показывает именно anthropic с нашими правками
    assert dlg.p_model.text() == "claude-opus-4-8"
    assert dlg.p_api_key.text() == "sk-test-456"
    dlg._save()

    assert config.provider("anthropic").model == "claude-opus-4-8"
    assert config.provider("anthropic").api_key == "sk-test-456"
    # ключ не «переехал» в соседние провайдеры
    assert config.provider("claude-cli").api_key == ""
    assert config.provider("openai").api_key == ""


def test_browsing_prompts_does_not_scramble(qtbot, config):
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)

    for i in range(dlg.prompt_list.count()):
        dlg.prompt_list.setCurrentRow(i)
    for i in reversed(range(dlg.prompt_list.count())):
        dlg.prompt_list.setCurrentRow(i)
    dlg._save()

    prompts = {p.name: p.text for p in config.prompts()}
    assert set(prompts) == {"Explain", "Translate", "Fix"}
    assert prompts["Explain"].startswith("Briefly explain")
    assert prompts["Translate"].startswith("Translate")
    assert prompts["Fix"].startswith("Fix")


def test_edit_prompt_text_persists(qtbot, config):
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)

    dlg.prompt_list.setCurrentRow(1)  # "Translate"
    dlg.prompt_text.setPlainText("Translate to French:")
    dlg.prompt_list.setCurrentRow(0)
    dlg._save()

    prompts = {p.name: p.text for p in config.prompts()}
    assert prompts["Translate"] == "Translate to French:"
    assert prompts["Explain"].startswith("Briefly explain")


def test_autostart_desktop_file_is_rendered(qtbot, config, tmp_path):
    import kortalk.settings_dialog as sd

    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)
    dlg.autostart.setChecked(True)
    dlg._save()

    text = sd.AUTOSTART_FILE.read_text(encoding="utf-8")
    # шаблон подставлен, а не записан как есть
    assert "{exec_path}" not in text
    assert any(line.startswith("Exec=") and len(line) > len("Exec=")
               for line in text.splitlines())


def test_dialog_builds_in_russian(qtbot, config):
    from kortalk.i18n import set_language

    set_language("ru")
    try:
        dlg = SettingsDialog(config)
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "Настройки — kortalk"
    finally:
        set_language("en")


def test_language_choice_is_saved(qtbot, config):
    dlg = SettingsDialog(config)
    qtbot.addWidget(dlg)
    dlg.language_combo.setCurrentIndex(dlg.language_combo.findData("ru"))
    dlg._save()
    assert config.get("language") == "ru"
