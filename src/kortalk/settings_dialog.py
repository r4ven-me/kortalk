"""Settings dialog: general, prompts & hotkeys, providers."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .config import Config, Prompt, Provider
from .i18n import tr
from .providers import check_provider

AUTOSTART_FILE = Path.home() / ".config" / "autostart" / "kortalk.desktop"

# Exec must be an absolute path: with a pipx install ~/.local/bin may not be
# in PATH yet when the session starts.
AUTOSTART_DESKTOP = """\
[Desktop Entry]
Type=Application
Name=Kortalk
Comment=Korvus AI popup for selected text
Exec={exec_path}
Icon={icon_path}
X-GNOME-Autostart-enabled=true
"""

# labels are tr() keys: translated when the dialog is built
PROVIDER_TYPES = [
    ("claude-cli", "Claude Code CLI"),
    ("anthropic", "Anthropic API"),
    ("openai", "OpenAI-compatible API"),
]

_KEY_FMT = QKeySequence.SequenceFormat.PortableText


def _prompt_label(p: Prompt) -> str:
    return f"{p.name}   —   {p.hotkey}" if p.hotkey else p.name


class SettingsDialog(QDialog):
    saved = Signal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("Settings — Kortalk"))
        self.resize(680, 520)
        theme.apply_window_theme(self)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), tr("General"))
        tabs.addTab(self._build_prompts_tab(), tr("Prompts"))
        tabs.addTab(self._build_providers_tab(), tr("Providers"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # -- "General" tab --------------------------------------------------------

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.language_combo = QComboBox()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Русский", "ru")
        lang_index = self.language_combo.findData(str(self.config.get("language")))
        self.language_combo.setCurrentIndex(max(0, lang_index))
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.language_combo, 1)
        lang_row.addWidget(QLabel("<i>" + tr("(applies fully after restart)") + "</i>"))
        form.addRow(tr("Language:"), lang_row)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem(tr("System"), "system")
        self.theme_combo.addItem("Nord Dark", "nord-dark")
        self.theme_combo.addItem("Nord Light", "nord-light")
        index = self.theme_combo.findData(str(self.config.get("theme")))
        self.theme_combo.setCurrentIndex(max(0, index))
        form.addRow(tr("Theme:"), self.theme_combo)

        font_row = QHBoxLayout()
        self.font_combo = QFontComboBox()
        family = str(self.config.get("font_family"))
        self.font_default = QCheckBox(tr("system default"))
        self.font_default.setChecked(not family)
        if family:
            self.font_combo.setCurrentText(family)
        self.font_combo.setEnabled(bool(family))
        self.font_default.toggled.connect(lambda on: self.font_combo.setEnabled(not on))
        self.font_size = QSpinBox()
        self.font_size.setRange(0, 32)
        self.font_size.setSpecialValueText(tr("auto"))
        self.font_size.setValue(int(self.config.get("font_size")))
        font_row.addWidget(self.font_default)
        font_row.addWidget(self.font_combo, 1)
        font_row.addWidget(QLabel(tr("size:")))
        font_row.addWidget(self.font_size)
        form.addRow(tr("Font:"), font_row)

        self.popup_width = QSpinBox()
        self.popup_width.setRange(300, 1200)
        self.popup_width.setValue(int(self.config.get("popup_width")))
        form.addRow(tr("Popup width, px:"), self.popup_width)

        self.popup_max_height = QSpinBox()
        self.popup_max_height.setRange(200, 1400)
        self.popup_max_height.setValue(int(self.config.get("popup_max_height")))
        form.addRow(tr("Popup max height, px:"), self.popup_max_height)

        self.timeout = QSpinBox()
        self.timeout.setRange(10, 1200)
        self.timeout.setValue(int(self.config.get("timeout")))
        form.addRow(tr("Request timeout, s:"), self.timeout)

        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(256, 128000)
        self.max_tokens.setSingleStep(1024)
        self.max_tokens.setValue(int(self.config.get("max_tokens")))
        form.addRow(tr("Max response tokens:"), self.max_tokens)

        self.autostart = QCheckBox(tr("Start at login"))
        self.autostart.setChecked(AUTOSTART_FILE.exists())
        form.addRow("", self.autostart)

        settings_path = tr("Settings file: {path}").format(path=self.config.file_path())
        form.addRow("", QLabel(f"<i>{settings_path}</i>"))
        return page

    # -- "Prompts" tab: prompt library + all hotkey assignment ----------------

    def _build_prompts_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)

        window_row = QHBoxLayout()
        window_row.addWidget(QLabel(tr("Open window:")))
        self.hotkey_window = QKeySequenceEdit(QKeySequence(self.config.hotkey("window")))
        window_row.addWidget(self.hotkey_window, 1)
        clear_window = QPushButton(tr("Clear"))
        clear_window.clicked.connect(self.hotkey_window.clear)
        window_row.addWidget(clear_window)
        outer.addLayout(window_row)

        split = QHBoxLayout()
        outer.addLayout(split, 1)

        left = QVBoxLayout()
        self.prompt_list = QListWidget()
        active_name = str(self.config.get("active_prompt"))
        for p in self.config.prompts():
            prompt = Prompt(p.name, p.text, p.hotkey)
            item = QListWidgetItem(_prompt_label(prompt))
            item.setData(Qt.ItemDataRole.UserRole, prompt)
            self.prompt_list.addItem(item)
        self.prompt_list.currentItemChanged.connect(self._prompt_row_changed)
        left.addWidget(self.prompt_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.clicked.connect(self._add_prompt)
        del_btn = QPushButton("−")
        del_btn.clicked.connect(self._remove_prompt)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        split.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel(tr("Name:")))
        self.prompt_name = QLineEdit()
        self.prompt_name.textEdited.connect(self._sync_prompt_item)
        right.addWidget(self.prompt_name)
        right.addWidget(QLabel(tr("Prompt text (the selection is appended after it):")))
        self.prompt_text = QPlainTextEdit()
        right.addWidget(self.prompt_text, 1)

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel(tr("Hotkey (popup with this prompt):")))
        self.prompt_hotkey = QKeySequenceEdit()
        self.prompt_hotkey.keySequenceChanged.connect(self._sync_prompt_item)
        hotkey_row.addWidget(self.prompt_hotkey, 1)
        hotkey_clear = QPushButton(tr("Clear"))
        hotkey_clear.clicked.connect(self.prompt_hotkey.clear)
        hotkey_row.addWidget(hotkey_clear)
        right.addLayout(hotkey_row)

        self.prompt_active = QCheckBox(tr("Default prompt (for tray/hotkey popup)"))
        right.addWidget(self.prompt_active)
        split.addLayout(right, 2)

        outer.addWidget(QLabel("<i>" + tr(
            "X11: keys are grabbed by the application directly.<br>"
            "Wayland: the system GlobalShortcuts portal is used —<br>"
            "the compositor may show a confirmation dialog."
        ) + "</i>"))

        self._loading_prompt = False
        self._active_prompt_name = active_name
        if self.prompt_list.count():
            row = 0
            for i in range(self.prompt_list.count()):
                if self.prompt_list.item(i).data(Qt.ItemDataRole.UserRole).name == active_name:
                    row = i
                    break
            self.prompt_list.setCurrentRow(row)
        self.prompt_active.toggled.connect(self._active_prompt_toggled)
        return page

    def _prompt_row_changed(self, current: QListWidgetItem | None,
                            previous: QListWidgetItem | None) -> None:
        # Same as providers: store the previous item before showing the new one.
        self._store_prompt(previous)
        self._show_prompt(current)

    def _show_prompt(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        self._loading_prompt = True
        p: Prompt = item.data(Qt.ItemDataRole.UserRole)
        self.prompt_name.setText(p.name)
        self.prompt_text.setPlainText(p.text)
        self.prompt_hotkey.setKeySequence(QKeySequence(p.hotkey))
        self.prompt_active.setChecked(p.name == self._active_prompt_name)
        self._loading_prompt = False

    def _store_prompt(self, item: QListWidgetItem | None) -> None:
        if item is None or self._loading_prompt:
            return
        p: Prompt = item.data(Qt.ItemDataRole.UserRole)
        p.name = self.prompt_name.text().strip() or p.name
        p.text = self.prompt_text.toPlainText().strip()
        p.hotkey = self.prompt_hotkey.keySequence().toString(_KEY_FMT)
        item.setData(Qt.ItemDataRole.UserRole, p)
        item.setText(_prompt_label(p))

    def _active_prompt_toggled(self, on: bool) -> None:
        if self._loading_prompt or not on:
            return
        self._active_prompt_name = self.prompt_name.text().strip()

    def _sync_prompt_item(self, *_args) -> None:
        # live-updates the list label while the name/hotkey fields are edited
        item = self.prompt_list.currentItem()
        if item is None or self._loading_prompt:
            return
        p: Prompt = item.data(Qt.ItemDataRole.UserRole)
        p.name = self.prompt_name.text().strip() or p.name
        p.hotkey = self.prompt_hotkey.keySequence().toString(_KEY_FMT)
        item.setData(Qt.ItemDataRole.UserRole, p)
        item.setText(_prompt_label(p))

    def _add_prompt(self) -> None:
        p = Prompt(name=tr("New prompt {n}").format(n=self.prompt_list.count() + 1), text="")
        item = QListWidgetItem(_prompt_label(p))
        item.setData(Qt.ItemDataRole.UserRole, p)
        self.prompt_list.addItem(item)
        self.prompt_list.setCurrentItem(item)

    def _remove_prompt(self) -> None:
        if self.prompt_list.count() <= 1:
            QMessageBox.information(self, "Kortalk", tr("Cannot delete the last prompt."))
            return
        self.prompt_list.takeItem(self.prompt_list.currentRow())

    # -- "Providers" tab ------------------------------------------------------

    def _build_providers_tab(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)

        left = QVBoxLayout()
        self.provider_list = QListWidget()
        for p in self.config.providers():
            item = QListWidgetItem(p.name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.provider_list.addItem(item)
        self.provider_list.currentItemChanged.connect(self._provider_row_changed)
        left.addWidget(self.provider_list)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+")
        add_btn.clicked.connect(self._add_provider)
        del_btn = QPushButton("−")
        del_btn.clicked.connect(self._remove_provider)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        layout.addLayout(left, 1)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        self.p_name = QLineEdit()
        self.p_name.textEdited.connect(self._sync_item_name)
        form.addRow(tr("Name:"), self.p_name)

        self.p_type = QComboBox()
        for type_id, label in PROVIDER_TYPES:
            self.p_type.addItem(tr(label), type_id)
        self.p_type.currentIndexChanged.connect(self._type_changed)
        self.p_type.currentIndexChanged.connect(self._update_status)
        form.addRow(tr("Type:"), self.p_type)

        self.p_model = QLineEdit()
        self.p_model.setPlaceholderText(tr("e.g. claude-opus-4-8 / gpt-4o / llama3"))
        self.p_model.textChanged.connect(self._update_status)
        form.addRow(tr("Model:"), self.p_model)

        self.p_api_key = QLineEdit()
        self.p_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.p_api_key.textChanged.connect(self._update_status)
        key_row = QHBoxLayout()
        key_row.addWidget(self.p_api_key)
        self.p_api_key_show = QPushButton("👁")
        self.p_api_key_show.setObjectName("iconButton")
        self.p_api_key_show.setToolTip(tr("Show/hide the API key"))
        self.p_api_key_show.setCheckable(True)
        self.p_api_key_show.setFixedWidth(40)
        self.p_api_key_show.toggled.connect(
            lambda on: self.p_api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row.addWidget(self.p_api_key_show)
        self.p_api_key_label = QLabel(tr("API key:"))
        form.addRow(self.p_api_key_label, key_row)

        self.p_base_url = QLineEdit()
        self.p_base_url.setPlaceholderText(tr("e.g. http://localhost:11434/v1"))
        self.p_base_url.textChanged.connect(self._update_status)
        self.p_base_url_label = QLabel("Base URL:")
        form.addRow(self.p_base_url_label, self.p_base_url)

        self.p_extra_args = QLineEdit()
        self.p_extra_args.setPlaceholderText(tr("extra claude arguments"))
        self.p_extra_args_label = QLabel(tr("CLI arguments:"))
        form.addRow(self.p_extra_args_label, self.p_extra_args)

        self.p_status = QLabel()
        form.addRow(tr("Status:"), self.p_status)

        self.active_check = QCheckBox(tr("Active provider (default)"))
        form.addRow("", self.active_check)

        layout.addWidget(form_widget, 2)

        self._loading = False
        if self.provider_list.count():
            self.provider_list.setCurrentRow(0)
        return page

    def _provider_row_changed(self, current: QListWidgetItem | None,
                              previous: QListWidgetItem | None) -> None:
        # IMPORTANT: store the form into the previous item first, then show
        # the new one. The reverse order wrote the new provider's data into
        # the previous one and scrambled the config.
        self._store_form(previous)
        self._show_provider(current)

    def _show_provider(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        self._loading = True
        p: Provider = item.data(Qt.ItemDataRole.UserRole)
        self.p_name.setText(p.name)
        self.p_type.setCurrentIndex(max(0, self.p_type.findData(p.type)))
        self.p_model.setText(p.model)
        self.p_api_key.setText(p.api_key)
        self.p_base_url.setText(p.base_url)
        self.p_extra_args.setText(" ".join(p.extra_args))
        self.active_check.setChecked(p.id == str(self.config.get("active_provider")))
        self._loading = False
        self._type_changed()
        self._update_status()

    def _store_form(self, item: QListWidgetItem | None) -> None:
        if item is None or self._loading:
            return
        p: Provider = item.data(Qt.ItemDataRole.UserRole)
        p.name = self.p_name.text().strip() or p.id
        p.type = self.p_type.currentData()
        p.model = self.p_model.text().strip()
        p.api_key = self.p_api_key.text().strip()
        p.base_url = self.p_base_url.text().strip()
        p.extra_args = self.p_extra_args.text().split()
        item.setData(Qt.ItemDataRole.UserRole, p)
        if self.active_check.isChecked():
            self.config.set("active_provider", p.id)

    def _type_changed(self) -> None:
        provider_type = self.p_type.currentData()
        is_cli = provider_type == "claude-cli"
        is_openai = provider_type == "openai"
        self.p_api_key.setVisible(not is_cli)
        self.p_api_key_label.setVisible(not is_cli)
        self.p_api_key_show.setVisible(not is_cli)
        self.p_base_url.setVisible(is_openai)
        self.p_base_url_label.setVisible(is_openai)
        self.p_extra_args.setVisible(is_cli)
        self.p_extra_args_label.setVisible(is_cli)

    def _update_status(self) -> None:
        provider = Provider(
            id="", name="", type=self.p_type.currentData() or "",
            model=self.p_model.text().strip(),
            api_key=self.p_api_key.text().strip(),
            base_url=self.p_base_url.text().strip(),
        )
        ok, message = check_provider(provider)
        self.p_status.setText(("✅ " if ok else "❌ ") + message)

    def _sync_item_name(self, text: str) -> None:
        item = self.provider_list.currentItem()
        if item is not None:
            item.setText(text)

    def _add_provider(self) -> None:
        existing = {
            self.provider_list.item(i).data(Qt.ItemDataRole.UserRole).id
            for i in range(self.provider_list.count())
        }
        n = 1
        while f"provider-{n}" in existing:
            n += 1
        p = Provider(id=f"provider-{n}", name=tr("New provider {n}").format(n=n), type="openai")
        item = QListWidgetItem(p.name)
        item.setData(Qt.ItemDataRole.UserRole, p)
        self.provider_list.addItem(item)
        self.provider_list.setCurrentItem(item)

    def _remove_provider(self) -> None:
        row = self.provider_list.currentRow()
        if row < 0 or self.provider_list.count() <= 1:
            QMessageBox.information(self, "Kortalk", tr("Cannot delete the last provider."))
            return
        item = self.provider_list.takeItem(row)
        p: Provider = item.data(Qt.ItemDataRole.UserRole)
        self.config.remove_provider(p.id)

    # -- saving ---------------------------------------------------------------

    def _save(self) -> None:
        self._store_form(self.provider_list.currentItem())
        self._store_prompt(self.prompt_list.currentItem())

        self.config.set("language", self.language_combo.currentData())
        self.config.set("theme", self.theme_combo.currentData())
        self.config.set(
            "font_family",
            "" if self.font_default.isChecked() else self.font_combo.currentText(),
        )
        self.config.set("font_size", self.font_size.value())
        self.config.set("popup_width", self.popup_width.value())
        self.config.set("popup_max_height", self.popup_max_height.value())
        self.config.set("timeout", self.timeout.value())
        self.config.set("max_tokens", self.max_tokens.value())

        prompts = [
            self.prompt_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.prompt_list.count())
        ]
        self.config.set_prompts(prompts)
        if not self.config.prompt_by_name(self._active_prompt_name) and prompts:
            self._active_prompt_name = prompts[0].name
        self.config.set("active_prompt", self._active_prompt_name)

        self.config.set_hotkey("window", self.hotkey_window.keySequence().toString(_KEY_FMT))

        for i in range(self.provider_list.count()):
            self.config.save_provider(
                self.provider_list.item(i).data(Qt.ItemDataRole.UserRole)
            )

        try:
            if self.autostart.isChecked():
                AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
                # shutil.which() can miss ~/.local/bin if this process itself
                # started before it was added to PATH — sys.argv[0] is how
                # the OS actually found us, so it's always resolvable.
                exec_path = shutil.which("kortalk") or str(Path(sys.argv[0]).resolve())
                icon_path = theme.install_icon_file()
                AUTOSTART_FILE.write_text(
                    AUTOSTART_DESKTOP.format(exec_path=exec_path, icon_path=icon_path),
                    encoding="utf-8",
                )
            elif AUTOSTART_FILE.exists():
                AUTOSTART_FILE.unlink()
        except OSError as exc:
            QMessageBox.warning(
                self, "Kortalk",
                tr("Failed to configure autostart: {error}").format(error=exc))

        self.saved.emit()
        self.accept()
