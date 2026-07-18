# kortalk

**Korvus AI popup** — приложение семейства **korvus** (korserver, korctl, kortalk).
AI-popup для выделенного текста в духе [Crow Translate](https://crow-translate.github.io/):
выделили текст мышью → нажали хоткей → у курсора всплыло окошко с ответом ИИ.
Живёт в трее, стримит ответы, поддерживает несколько провайдеров.

## Возможности

- **Popup у курсора** — скруглённые углы, Markdown-рендер ответа с подсветкой
  блоков кода, стриминг токенов в реальном времени, выделение/копирование
  текста. Закрывается по клику за пределами окна или `Escape`.
- **Окно в два столбца** — редактируемый промпт+текст слева, ответ справа,
  выбор провайдера на панели, `Ctrl+Enter` для отправки.
- **Трей** — приложение резидентно: монохромный ворон (Corvus — символ korvus), ЛКМ = popup
  с выделением, меню с библиотекой промптов, окном, настройками и выходом.
- **Глобальные хоткеи внутри приложения** — назначаются в настройках
  (по умолчанию `Ctrl+Alt+C` popup, `Ctrl+Alt+W` окно). X11 — прямой
  перехват XGrabKey, Wayland — системный портал GlobalShortcuts.
  Никаких внешних утилит и настройки DE не требуется.
- **Библиотека промптов** — несколько именованных промптов в настройках;
  промпт по умолчанию для хоткея, любой другой — из подменю трея.
- **Провайдеры ИИ**:
  - **Claude Code CLI** — через `claude -p`, без API-ключа (по умолчанию);
  - **Anthropic API** — официальный SDK, стриминг (нужен API-ключ);
  - **OpenAI-совместимые API** — OpenAI, **Ollama**, LM Studio, OpenRouter,
    Groq, DeepSeek и любой другой сервис с `/chat/completions` (настраивается
    `base URL` + модель + ключ, для локальных серверов ключ не нужен).
- **Графические настройки** — промпты, клавиши, тема, шрифт, размеры popup,
  таймауты, менеджер провайдеров с ключами, автозапуск при входе.
  Конфиг — человекочитаемый YAML (`~/.config/kortalk/config.yaml`).
- **Тема и шрифты** — по умолчанию следует теме окружения (Qt), опционально
  Nord Dark / Nord Light.
- **Два языка интерфейса** — английский (по умолчанию) и русский:
  Настройки → Общие → Language.
- **PRIMARY-выделение читается нативно** через Qt — xclip/xsel/wl-clipboard
  больше не нужны. Работает на X11 и Wayland.

## Установка

```bash
pipx install kortalk
kortalk --check     # диагностика: провайдеры, трей, PRIMARY-выделение
```

Никаких системных пакетов и флагов вроде `--system-site-packages` не
требуется — Qt (PySide6) ставится из PyPI. Единственная внешняя зависимость —
[Claude Code CLI](https://docs.claude.com), и только если используете
провайдер `claude-cli`.

> Конфиг хранится в `~/.config/kortalk/config.yaml` и редактируется через
> настройки (или руками). Конфиги прежних версий (toml/ini) не используются.

## Использование

```bash
kortalk                       # запустить демона (трей + хоткеи)
kortalk --popup               # popup у курсора (для скриптов/сторонних хоткеев)
kortalk "Переведи на английский:"   # popup с разовым промптом
kortalk --window              # окно в два столбца (алиас: --split)
kortalk --provider ollama     # разовый запрос через конкретный провайдер
kortalk --settings            # настройки
kortalk --quit                # завершить работающий экземпляр
kortalk --check               # диагностика
```

Основной сценарий: `kortalk` запускает демона, дальше всё делается из трея
и по глобальным хоткеям. CLI-флаги оставлены для скриптов — они мгновенно
доставляются работающему экземпляру через локальный сокет.

Текст берётся из **PRIMARY selection** — достаточно выделить его мышью,
Ctrl+C не нужен.

## Хоткеи

Назначаются в Настройки → Клавиши, работают глобально:

- **X11** — приложение перехватывает клавиши напрямую (XGrabKey),
  работает в любом WM/DE без настройки.
- **Wayland** — используется XDG Desktop Portal (GlobalShortcuts);
  GNOME/KDE покажут диалог подтверждения биндинга. Если портал недоступен
  (минималистичные композиторы) — пользуйтесь треем или повесьте
  `kortalk --popup` на хоткей средствами композитора.

## Настройка провайдеров

`kortalk --settings` → вкладка «Провайдеры». Примеры:

| Провайдер | Тип | Base URL | Модель |
|---|---|---|---|
| Claude Code CLI | claude-cli | — | *(пусто = дефолт CLI)* |
| Anthropic API | anthropic | — | `claude-opus-4-8` |
| OpenAI | openai | `https://api.openai.com/v1` | `gpt-4o` |
| Ollama | openai | `http://localhost:11434/v1` | `llama3`, `qwen3`, … |
| LM Studio | openai | `http://localhost:1234/v1` | имя загруженной модели |
| OpenRouter | openai | `https://openrouter.ai/api/v1` | любой из каталога |

«Активный провайдер» используется по умолчанию для popup; в окне провайдер
выбирается в выпадающем списке, разово — флагом `--provider <id>`.

## Автозапуск

Настройки → Общие → «Запускать при входе в систему» — создаёт
`~/.config/autostart/kortalk.desktop` с командой `kortalk`.

## Разработка

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
QT_QPA_PLATFORM=offscreen .venv/bin/kortalk --check   # headless-проверка
```

Структура: [src/kortalk/](src/kortalk/) — `app.py` (CLI, трей, IPC),
`providers.py` (воркеры ИИ), `hotkeys.py` (XGrabKey / портал),
`windows.py` (popup и главное окно), `settings_dialog.py`, `config.py`,
`theme.py`.

## Лицензия

MIT, см. [LICENSE](LICENSE).

## Разработка

```bash
git clone https://github.com/r4ven-me/kortalk && cd kortalk
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]' ruff
ruff check .                        # линтер
QT_QPA_PLATFORM=offscreen pytest    # тесты (headless)
```

Логи работающего приложения: `~/.local/state/kortalk/kortalk.log`
(подробный вывод — флаг `--debug`).
