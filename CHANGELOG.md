# Changelog

## Unreleased

### Fixes

- **Ctrl+C (SIGINT) and SIGTERM now quit the daemon cleanly.** The Qt event
  loop never let the Python signal handler run; a periodic timer now wakes
  the interpreter and the handler shuts the application down.
- **Quitting from the tray is instant.** The tray icon is hidden first, and
  cancelling an in-flight request now closes its network stream, so workers
  blocked on a read no longer delay shutdown by up to two seconds.
- **The API-key show/hide button no longer floats with no field next to
  it.** For Claude Code CLI (no key needed) it was left visible even
  though the key field and its label were hidden; it now hides with them.
- **Settings shows the raven icon in the taskbar/window switcher** instead
  of the WM's generic placeholder — only `MainWindow` set its own icon
  before; the icon is now also set application-wide so every window
  (current and future) gets it by default.
- **`kortalk --version` matches `pyproject.toml`.** The version used to be
  hardcoded a second time in `kortalk/__init__.py` and drifted out of sync
  with every release; it's now read from the installed package's own
  metadata, so there's exactly one place it can come from.
- **`claude` (and any other external tool) is now found when kortalk is
  launched from the applications menu or autostart.** Such launches
  inherit a minimal PATH that skips `.bashrc`/`.zshrc`, where most people
  add `~/.local/bin` and friends — kortalk now merges in its login shell's
  PATH once at startup, the same way a terminal would see it.
- **The applications-menu entry and the autostart entry always get a
  working `Exec=` path.** `shutil.which("kortalk")` can return nothing if
  this very process started before `~/.local/bin` was on PATH; both now
  fall back to resolving `sys.argv[0]`, which is how the OS actually found
  the running process. Also dropped the extra `Categories=` entry that
  `desktop-file-validate` flagged as risking a duplicate menu item.

### Changes

- **Popup windows can be dragged** — click anywhere on the card that isn't
  a button or the response text and move it; it stays put until you close
  it or press `Escape`.
- **Tray left click now opens the two-column window** instead of the
  popup (still reachable from the tray menu: "Popup with selection");
  clicking again while it's open hides it back to the tray instead of
  just re-focusing it.
- **"Open in window" no longer loses context** — the original prompt and
  selected text now go to the left pane along with the answer on the
  right, instead of leaving the left pane empty.
- **Provider availability status in Settings → Providers** — an inline
  ✅/❌ line (same checks as `kortalk --check`: CLI in PATH, key/URL/model
  set) that updates live as you edit the type, model, key or URL.
- **A real applications-menu entry.** pip/pipx only puts the `kortalk`
  binary on PATH, so the app now writes its own launcher (with the raven
  icon) to `~/.local/share/applications/kortalk.desktop` on every daemon
  start; the autostart entry uses the same icon instead of a generic one.
- **Settings dialog tabs no longer look stuck together** — visible gaps
  and borders between them.
- **Hotkeys merged into the Prompts tab.** The separate Hotkeys tab is
  gone; every prompt in Settings → Prompts has its own hotkey field (opens
  the popup with that prompt applied to the current selection, list shows
  the assignment), plus one "Open window" hotkey at the top of the same
  tab. Configs from earlier versions are migrated automatically: a
  configured "popup" hotkey moves to the active prompt if it doesn't
  already have one of its own.
- **New tray icon** — a raven silhouette
  ([source](https://www.svgrepo.com/svg/156257/raven)), recoloured at
  runtime for light and dark themes, instead of the previous hand-drawn
  bird.
- **Settings dialog and the split window now use the same surface colours
  as the popup**, regardless of the selected theme (system/Nord), so all
  three windows read as one flat, consistent surface instead of drifting
  apart under the native/system palette. Nord Dark itself is also darker —
  window backgrounds use a deeper Polar Night shade.
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
