"""Interface localization: English (default) and Russian.

Strings in the code are written in English; tr() returns the Russian
translation when "ru" is selected in Settings. CLI help and logs are
always English.
"""

from __future__ import annotations

_LANGUAGES = ("en", "ru")
_current = "en"


def set_language(language: str) -> None:
    global _current
    _current = language if language in _LANGUAGES else "en"


def current_language() -> str:
    return _current


def tr(text: str) -> str:
    if _current == "ru":
        return RU.get(text, text)
    return text


RU: dict[str, str] = {
    # -- tray / app -----------------------------------------------------------
    "Popup with selection": "Popup с выделением",
    "Popup with prompt": "Popup с промптом",
    "Open window": "Открыть окно",
    "Settings": "Настройки",
    "Quit": "Выход",
    "Hotkeys unavailable: {error}": "Хоткеи недоступны: {error}",
    # -- windows --------------------------------------------------------------
    "*Thinking…*": "*Думаю…*",
    "*(empty response)*": "*(пустой ответ)*",
    "Error": "Ошибка",
    "Copy": "Копировать",
    "Open in window": "В окно",
    "Provider:": "Провайдер:",
    "Prompt + text:": "Промпт + текст:",
    "Send (Ctrl+Enter)": "Отправить (Ctrl+Enter)",
    "Response:": "Ответ:",
    "Ready": "Готов",
    "Done": "Готово",
    "Requesting {name}…": "Запрос к {name}…",
    "Provider not found — check settings": "Провайдер не найден — проверьте настройки",
    # -- dialog mode ------------------------------------------------------------
    "Dialog": "Диалог",
    "Dialog mode: keeps the conversation and its context across "
    "messages. The quick panel stays untouched for fast one-off asks.":
        "Режим диалога: сохраняет переписку и её контекст между сообщениями. "
        "Обычная панель при этом не меняется и остаётся для быстрых разовых запросов.",
    "Dialog — context is kept between messages": "Диалог — контекст сохраняется между сообщениями",
    "New dialog": "Новый диалог",
    "Message… (Ctrl+Enter to send)": "Сообщение… (Ctrl+Enter — отправить)",
    "You": "Вы",
    "Assistant": "Ассистент",
    "Dialog mode — context is kept between messages.":
        "Режим диалога — контекст сохраняется между сообщениями.",
    "New dialog started": "Начат новый диалог",
    # -- settings: general ----------------------------------------------------
    "Settings — kortalk": "Настройки — kortalk",
    "General": "Общие",
    "Prompts": "Промпты",
    "Providers": "Провайдеры",
    "Language:": "Язык:",
    "(applies fully after restart)": "(полностью применяется после перезапуска)",
    "Theme:": "Тема:",
    "System": "Как в системе",
    "system default": "системный",
    "auto": "авто",
    "size:": "размер:",
    "Font:": "Шрифт:",
    "Popup width, px:": "Ширина popup, px:",
    "Popup max height, px:": "Макс. высота popup, px:",
    "Request timeout, s:": "Таймаут запроса, сек:",
    "Max response tokens:": "Макс. токенов ответа:",
    "Start at login": "Запускать при входе в систему",
    "Settings file: {path}": "Файл настроек: {path}",
    # -- settings: prompts ----------------------------------------------------
    "Name:": "Название:",
    "Prompt text (the selection is appended after it):":
        "Текст промпта (выделение добавляется после него):",
    "Hotkey (popup with this prompt):": "Клавиша (popup с этим промптом):",
    "Clear": "Очистить",
    "Default prompt (for tray/hotkey popup)":
        "Промпт по умолчанию (для popup из трея/хоткея)",
    "New prompt {n}": "Новый промпт {n}",
    "Cannot delete the last prompt.": "Нельзя удалить последний промпт.",
    "Open window:": "Открыть окно:",
    "X11: keys are grabbed by the application directly.<br>"
    "Wayland: the system GlobalShortcuts portal is used —<br>"
    "the compositor may show a confirmation dialog.":
        "X11: клавиши перехватываются приложением напрямую.<br>"
        "Wayland: используется системный портал GlobalShortcuts —<br>"
        "компоситор может показать диалог подтверждения.",
    # -- settings: providers --------------------------------------------------
    "OpenAI-compatible API": "OpenAI-совместимый API",
    "Type:": "Тип:",
    "Model:": "Модель:",
    "API key:": "API-ключ:",
    "CLI arguments:": "Аргументы CLI:",
    "e.g. claude-opus-4-8 / gpt-4o / llama3": "напр. claude-opus-4-8 / gpt-4o / llama3",
    "e.g. http://localhost:11434/v1": "напр. http://localhost:11434/v1",
    "extra claude arguments": "доп. аргументы claude",
    "Active provider (default)": "Активный провайдер (по умолчанию)",
    "New provider {n}": "Новый провайдер {n}",
    "Cannot delete the last provider.": "Нельзя удалить последний провайдер.",
    "Failed to configure autostart: {error}": "Не удалось настроить автозапуск: {error}",
    "Show/hide the API key": "Показать/скрыть API-ключ",
    "Status:": "Статус:",
    # -- provider availability status (Settings → Prompts... → Providers) -----
    "claude found in PATH.": "claude найден в PATH.",
    "claude not found in PATH — install Claude Code CLI.":
        "claude не найден в PATH — установите Claude Code CLI.",
    "API key is not set.": "Не задан API-ключ.",
    "API key is set.": "API-ключ задан.",
    "Base URL is not set.": "Не задан Base URL.",
    "Model is not set.": "Не задана модель.",
    "Base URL, model and key are set.": "Base URL, модель и ключ заданы.",
    "Unknown provider type.": "Неизвестный тип провайдера.",
    # -- provider errors ------------------------------------------------------
    "Unknown provider type: {type}": "Неизвестный тип провайдера: {type}",
    "Unexpected error: {error}": "Непредвиденная ошибка: {error}",
    "Claude Code CLI (`claude`) not found in PATH.\nInstall: https://docs.claude.com":
        "Claude Code CLI (`claude`) не найден в PATH.\nУстановка: https://docs.claude.com",
    "claude did not respond within {timeout} s.": "claude не ответил за {timeout} сек.",
    "claude exited with an error:\n{error}": "claude завершился с ошибкой:\n{error}",
    "The `anthropic` package is not installed (pip install anthropic).":
        "Пакет `anthropic` не установлен (pip install anthropic).",
    "Anthropic API key is not set — open Settings → Providers.":
        "Не задан API-ключ Anthropic — откройте Настройки → Провайдеры.",
    "The model declined the request (safety refusal).":
        "Модель отклонила запрос (safety refusal).",
    "Invalid Anthropic API key.": "Неверный API-ключ Anthropic.",
    "Model “{model}” not found — check the name in settings.":
        "Модель «{model}» не найдена — проверьте имя в настройках.",
    "Anthropic rate limit exceeded — wait and retry.":
        "Превышен лимит запросов Anthropic — подождите и повторите.",
    "Anthropic API error ({code}): {message}": "Ошибка Anthropic API ({code}): {message}",
    "Cannot connect to api.anthropic.com — check your network.":
        "Нет соединения с api.anthropic.com — проверьте сеть.",
    "Provider base URL is not set — open Settings → Providers.":
        "Не задан base URL провайдера — откройте Настройки → Провайдеры.",
    "Model is not set — open Settings → Providers.\n"
    "For Ollama: the name of an installed model (see `ollama list`).":
        "Не задана модель — откройте Настройки → Провайдеры.\n"
        "Для Ollama: имя установленной модели (см. `ollama list`).",
    "API key is not set — open Settings → Providers.":
        "Не задан API-ключ — откройте Настройки → Провайдеры.",
    "API error ({code}): {message}": "Ошибка API ({code}): {message}",
    "Cannot connect to {url}: {reason}": "Нет соединения с {url}: {reason}",
    "Is Ollama running? (`ollama serve`)": "Ollama запущен? (`ollama serve`)",
    "The provider did not respond within {timeout} s.":
        "Провайдер не ответил за {timeout} сек.",
}
