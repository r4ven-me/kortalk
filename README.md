# kortalk

**Korvus AI popup** — part of the **korvus** family apps.
An AI popup for selected text in the spirit of
[Crow Translate](https://crow-translate.github.io/): select text with the
mouse → press a hotkey → a window with the AI response pops up near the
cursor. Lives in the tray, streams responses, supports multiple providers.

## Features

- **Popup near the cursor** — rounded corners, Markdown rendering with
  highlighted code blocks, real-time token streaming, text
  selection/copying. Closes on an outside click or `Escape`.
- **Two-column window** — editable prompt+text on the left, response on the
  right, provider selector in the toolbar, `Ctrl+Enter` to send.
- **Tray** — the application is resident: a monochrome raven icon (Corvus —
  the korvus emblem), left click = popup with the selection, a menu with
  the prompt library, the window, settings and quit.
- **Global hotkeys inside the application** — one prompt-to-key table in
  Settings → Prompts: each prompt has its own hotkey that opens the popup
  with that prompt and the current selection (the default prompt, "Explain",
  ships with `Ctrl+Alt+C`), plus a separate hotkey to open the two-column
  window (`Ctrl+Alt+W` by default). X11 — direct XGrabKey interception,
  Wayland — the system GlobalShortcuts portal. No external tools or DE
  configuration required.
- **Prompt library** — any number of named prompts in Settings, each with
  its own hotkey and reachable from the tray submenu; one is marked as the
  default (used by the tray's left-click popup).
- **AI providers**:
  - **Claude Code CLI** — via `claude -p`, no API key (default);
  - **Anthropic API** — official SDK, streaming (API key required);
  - **OpenAI-compatible APIs** — OpenAI, **Ollama**, LM Studio, OpenRouter,
    Groq, DeepSeek and any other service with `/chat/completions`
    (configured with `base URL` + model + key; local servers need no key).
- **Graphical settings** — prompts, hotkeys, theme, font, popup sizes,
  timeouts, a provider manager with keys, autostart at login.
  The config is human-readable YAML (`~/.config/kortalk/config.yaml`).
- **Theme and fonts** — follows the environment theme (Qt) by default,
  optionally Nord Dark / Nord Light.
- **Two interface languages** — English (default) and Russian:
  Settings → General → Language.
- **PRIMARY selection is read natively** via Qt — xclip/xsel/wl-clipboard
  are not needed. Works on X11 and Wayland.

## Installation

```bash
pipx install kortalk
kortalk --check     # diagnostics: providers, tray, PRIMARY selection
```

No system packages or flags like `--system-site-packages` are required —
Qt (PySide6) comes from PyPI. The only external dependency is
[Claude Code CLI](https://docs.claude.com), and only if you use the
`claude-cli` provider.

> The config lives in `~/.config/kortalk/config.yaml` and is edited via
> Settings (or by hand). Configs of older versions (toml/ini) are not used.

## Usage

```bash
kortalk                       # start the daemon (tray + hotkeys)
kortalk --popup               # popup near the cursor (for scripts/external hotkeys)
kortalk "Translate to English:"   # popup with a one-off prompt
kortalk --window              # two-column window (alias: --split)
kortalk --provider ollama     # one-off request through a specific provider
kortalk --settings            # settings
kortalk --quit                # quit the running instance
kortalk --check               # diagnostics
```

The main scenario: `kortalk` starts the daemon, everything else is done
from the tray and via global hotkeys. The CLI flags are kept for scripting —
they are delivered to the running instance instantly over a local socket.

The text is taken from the **PRIMARY selection** — selecting it with the
mouse is enough, no Ctrl+C needed.

## Hotkeys

Assigned in Settings → Prompts — the "Open window" hotkey at the top of the
tab, and one hotkey per prompt in the list below it. All of them work
globally:

- **X11** — the application grabs the keys directly (XGrabKey), works in
  any WM/DE without configuration.
- **Wayland** — the XDG Desktop Portal (GlobalShortcuts) is used;
  GNOME/KDE will show a binding confirmation dialog. If the portal is not
  available (minimalist compositors) — use the tray or bind
  `kortalk --popup` to a hotkey in your compositor.

## Provider setup

`kortalk --settings` → Providers tab. Examples:

| Provider | Type | Base URL | Model |
|---|---|---|---|
| Claude Code CLI | claude-cli | — | *(empty = CLI default)* |
| Anthropic API | anthropic | — | `claude-opus-4-8` |
| OpenAI | openai | `https://api.openai.com/v1` | `gpt-4o` |
| Ollama | openai | `http://localhost:11434/v1` | `llama3`, `qwen3`, … |
| LM Studio | openai | `http://localhost:1234/v1` | name of the loaded model |
| OpenRouter | openai | `https://openrouter.ai/api/v1` | anything from the catalog |

The "active provider" is used by default for the popup; in the window the
provider is picked from a dropdown, for one-off requests — with
`--provider <id>`.

## Autostart

Settings → General → "Start at login" — creates
`~/.config/autostart/kortalk.desktop` running `kortalk`.

## Development

```bash
git clone https://github.com/r4ven-me/kortalk && cd kortalk
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]' ruff
ruff check .                        # linter
QT_QPA_PLATFORM=offscreen pytest    # tests (headless)
QT_QPA_PLATFORM=offscreen kortalk --check   # headless diagnostics
```

Layout: [src/kortalk/](src/kortalk/) — `app.py` (CLI, tray, IPC),
`providers.py` (AI workers), `hotkeys.py` (XGrabKey / portal),
`windows.py` (popup and main window), `settings_dialog.py`, `config.py`,
`theme.py`.

Logs of the running application: `~/.local/state/kortalk/kortalk.log`
(verbose output — the `--debug` flag).

## Credits

The tray/window raven icon is based on the
["raven" icon](https://www.svgrepo.com/svg/156257/raven) from SVG Repo,
recoloured at runtime to match the active theme.

## License

MIT, see [LICENSE](LICENSE).
