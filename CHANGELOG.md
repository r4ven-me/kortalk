# Changelog

## Unreleased

### Fixes

- **Ctrl+C (SIGINT) and SIGTERM now quit the daemon cleanly.** The Qt event
  loop never let the Python signal handler run; a periodic timer now wakes
  the interpreter and the handler shuts the application down.
- **Quitting from the tray is instant.** The tray icon is hidden first, and
  cancelling an in-flight request now closes its network stream, so workers
  blocked on a read no longer delay shutdown by up to two seconds.

### Changes

- **Per-prompt global hotkeys.** Every prompt in Settings → Prompts can get
  its own key combination that opens the popup with that prompt applied to
  the current selection. The tray submenu shows the assigned hotkeys.
- **New tray icon** — a raven head in profile with a heavy bill instead of
  the cartoonish full-body bird.
- **Nord Dark is darker** — window backgrounds use a deeper Polar Night
  shade; windows get a 1px border so they stand out against same-coloured
  backgrounds.
- All source comments, the README and the changelog are English-only; the
  interface still ships in English and Russian (Settings → General →
  Language).

## 0.4.0 — 2026-07-17

### Fixes

- **Critical: the settings dialog scrambled providers and prompts.**
  When switching items in the lists, the new item's data was written into
  the previous one (wrong `currentItemChanged` handler order). The config
  got corrupted by simply browsing the list and pressing Save, including
  an API key migrating into a foreign provider.
- Configs corrupted by that bug are detected automatically on startup: the
  provider section is reset to defaults with a warning (API keys must be
  entered again).
- Closing an old popup no longer clears the reference to the new popup.
- Autostart writes an absolute `kortalk` path into the `.desktop` file
  (with a pipx install `~/.local/bin` may be missing from PATH during
  session startup).

### Changes

- **Project renamed: crow-ai → kortalk** (the korvus family: korserver,
  korctl, kortalk). New names: the `kortalk` command, the `kortalk` PyPI
  package, the `~/.config/kortalk/config.yaml` config, logs in
  `~/.local/state/kortalk/`. The old crow-ai name is not used anywhere; the
  config is not picked up from the old path — move `config.yaml` manually
  if needed.
- **Two interface languages: English (default) and Russian.** Switch in
  Settings → General → Language (applies fully after a restart). Default
  prompts and providers for new installs are English; CLI help and logs are
  English only.
- The config file is created with `600` permissions — it may contain API
  keys.
- Logging to `~/.local/state/kortalk/kortalk.log` (rotation 1 MB × 3);
  `--debug` mirrors the log to stderr.
- The response token limit for the Anthropic API is configurable
  (Settings → General → "Max response tokens", default 64000).
- Tests (pytest + pytest-qt), linter (ruff) and CI (GitHub Actions,
  Python 3.9 and 3.13); PyPI publishing on `v*` tags via trusted
  publishing.
