# Changelog

## Unreleased

### Fixes

- **A hotkey bound to a punctuation key (`` ` ``, `-`, `=`, `[`, `;`, `/`, …)
  silently failed to register on X11** — e.g. rebinding "Open window" to
  something like `Alt+\`` made it stop working entirely. Unlike a
  letter/digit, whose X11 keysym value happens to equal its ASCII code,
  punctuation keys have their own symbolic keysym names (`` ` `` is
  `grave`, `-` is `minus`, …) that the raw character doesn't resolve to —
  the grab was requested for a nonexistent keysym and just never fired,
  with nothing surfaced beyond a log line. All the standard punctuation
  keys are now mapped to their real X11 keysym names.
- **The "Open window" hotkey now shows/hides the window like a tray
  click**, instead of only ever (re)focusing it — pressing it again while
  the window already had focus visibly did nothing, which read as the
  shortcut not working.
- **Selected/pasted text starting with `-` no longer breaks the Claude
  Code CLI provider.** A YAML document, a CLI flag, anything starting with
  a dash — claude's own argument parser (Commander.js) reads a leading `-`
  on the prompt as an attempted option of its own rather than the prompt's
  value, failing with `error: unknown option '...'` instead of ever
  reaching the model. The prompt is now always the last argv element,
  preceded by a literal `--`, which is the standard way to tell an
  argument parser "everything after this is a literal value, not an
  option" — so its content can no longer be misread as a flag.
- **Scrolling the conversation with the mouse wheel no longer randomly
  stops working over plain text.** A fenced code block's own scroll area
  already forwarded vertical wheel input to the conversation instead of
  eating it; every ordinary text paragraph — a `QTextBrowser` in its own
  right — never got the same fix, so the wheel silently did nothing
  wherever the cursor happened to be over prose rather than a code block.
- **The conversation no longer visibly jerks up and down while sending a
  message or right as a response finishes.** After a full rebuild, each
  block's real (wrapped) height only becomes known once Qt has actually
  laid it out, over a few event-loop turns — the auto-scroll-to-bottom
  logic used to re-snap to the bottom on *every* one of those turns,
  including the transient, not-yet-final heights along the way, so the
  view visibly jumped past the real content and then jerked back a frame
  later. It now only ever moves the scrollbar on the first turn (so
  following a new answer still feels immediate) and the last one (a
  guaranteed final correction), never on the turns in between.
- **The popup no longer flickers/resizes back and forth while an answer is
  streaming in.** Its auto-height-to-content logic reacted to every single
  content-change signal, including several transient, not-yet-settled ones
  fired in a row by each live-preview re-render tick — each one resized
  the actual OS window, which showed as a rapid shrink/grow flicker roughly
  every 400ms. These are now debounced into a single resize once a burst
  of changes actually settles.

### Changes

- **The popup no longer closes on an outside click or losing focus**, and
  now stays on top of every other window — the only way to close it is
  its own ✕ button. It previously used Qt's native `Popup` window type,
  which auto-closes on exactly those triggers; `Escape` closing it is also
  gone, along with that auto-close behaviour.
- **Empty dialog state**: a fresh/empty conversation now shows a small
  raven illustration and "Kar-kar…" instead of an isolated card repeating
  "Dialog mode…" right under a toolbar label already saying almost the
  same thing.
- **New: "Quick questions"** — a pinned entry at the top of the dialog
  window's session list (bold, amber, always there) for one-off questions
  that shouldn't accumulate context. Every message you send in it goes to
  the provider on its own, without earlier turns resent alongside it, even
  though the visible transcript still keeps them all so you can scroll
  back. It's never written to `session.sqlite3` — its history lives only
  in memory and starts empty again on every restart. "Delete" on it clears
  its history instead of removing the entry.
- **New: attachments in the dialog window** — paste an image with
  Ctrl+V, drag and drop images/text files onto the message box, or use the
  new 📎 button next to it to pick files instead; they show up as
  removable chips above the box and go to the model with your next
  message. Clicking an image chip — before sending, or its `📎 name` link
  in the transcript afterwards — opens it full-size. Large images are
  downscaled to 1568px on the long edge before sending/storing; text files
  (code, logs, configs — detected by content, not extension) are inlined
  into the prompt with a size cap. Works with all three provider types,
  including Claude Code CLI, which has no native way to take image bytes:
  images are written to a per-request temp directory and the CLI is run
  with it as its working directory, so its own Read tool can see them
  without an interactive approval prompt print mode could never answer.
  Popup windows don't have a composer, so this is dialog-mode only.
- **Streamed responses now render as formatted Markdown while they're
  still arriving**, instead of showing raw `**`/`` ``` ``/`#` syntax until
  the answer finishes. The in-progress text is periodically re-rendered
  through the same block-based formatter used for the final answer
  (about twice a second), reusing the existing scroll-position handling
  so this doesn't reintroduce any jumping. A fenced code block still
  displays as plain text until its closing ` ``` ` actually arrives —
  there's no way to syntax-highlight a block that isn't finished yet.
- **New: per-prompt provider/model** — Settings → Prompts now has a "Model
  for this prompt's popup" picker next to each prompt's hotkey. Left on
  "(active provider)" a prompt behaves as before; pinned to a specific
  provider, its popup always answers through that one instead — handy for
  e.g. keeping a fast/cheap model on one hotkey and a stronger one on
  another. This is popup-only and never touches the provider selected in
  the two-column window, which keeps whatever you last picked there.
- **The popup can now be resized by dragging its edges/corners**, using
  the window manager's own native resize instead of manual geometry
  math — the usual resize cursors just work. It still always *opens* at
  the size derived from Settings (width, and height fit to the answer up
  to "Max popup height"); once you resize a given popup by hand, that one
  keeps the size you gave it instead of snapping back as more of the
  answer streams in. The next popup you open starts fresh at the
  configured size again.
- **The Stop button is now red while a popup is generating an answer**,
  and dims back to the ordinary muted button style once it's done —
  previously it looked identical (and equally unremarkable) in both
  states.

## 1.1.0 — 2026-08-05

Per-version entries lapsed between 0.4.0 and this release (~30 tagged
versions) — rather than inventing per-version history from commit messages
that don't carry it, this section summarizes the accumulated user-facing
changes across that whole span in one place. Releases from here on get a
normal per-version entry again.

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

- **Dialog mode**: the two-column window keeps a running, multi-turn
  conversation — every earlier turn is resent as context with the next
  message, not just the latest one. Conversations persist across restarts
  (SQLite, `~/.local/share/kortalk/session.sqlite3`), with a session list
  to create, switch between, and delete dialogs.
- **Claude Code CLI tool-use controls** — "Web search" and "Run commands"
  toggles (dialog window toolbar and Settings → General) build the CLI's
  `--allowedTools`/`--disallowedTools` flags; web search is on by default,
  running Bash/Edit/Write is off by default and must be opted into.
- **Readable line length for responses** — text and code in the popup and
  dialog window are capped and centered at a configurable width
  (Settings → General → "Max response width"), Obsidian-style, instead of
  stretching edge to edge in a wide window.
- **Redesigned fenced code blocks** — a language label, a Copy button, a
  line-number gutter, indent guides, and their own independent horizontal
  scrollbar, so an unwrappable long line no longer forces the whole window
  wider. The code font is now configurable separately from the UI font.
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
