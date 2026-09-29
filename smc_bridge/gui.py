"""Qt mapping surface. Visual controls edit mappings; they do not send MIDI."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from .actions import MEDIA_ACTIONS
from .bridge import KEY_NOTES
from .config import Config, KeyAction, Mapping, load, save, validate
from . import plugins

ACTION_CHOICES = (("", "Nothing"), ("midi", "MIDI CC to jack_mixer"), ("media", "Media command"), ("command", "Shell command"), ("plugin", "Plugin"))
ROUTE_CHOICES = (("mixer", "jack_mixer"), ("plugin", "Plugin"))
MODE_CHOICES = (("toggle", "Toggle (press on, press off)"), ("momentary", "Momentary (on while held)"))
STRIP_KEY_BUTTONS = (("mute", "M", "Mute"), ("solo", "S", "Solo"), ("rec", "R", "R button"), ("select", "□", "Square (Select) button"))
# Exact left-to-right symbol order from the supplied hardware photo.
TRANSPORT_BUTTONS = (
    ("play", "▶", "Play"), ("pause", "Ⅱ", "Pause"), ("record", "●", "Record"),
    ("rewind", "◀◀", "Rewind"), ("fast_forward", "▶▶", "Fast forward"),
    ("bank_left", "≪", "Double chevron left"), ("bank_right", "≫", "Double chevron right"),
    ("up", "△", "Up"), ("down", "▽", "Down"), ("left", "◁", "Left"), ("right", "▷", "Right"),
)
KEY_NAMES = {
    **{f"strip{n}.{kind}": f"Strip {n:02} · {name}" for n in range(1, 9) for kind, _, name in STRIP_KEY_BUTTONS},
    **{f"transport.{kind}": f"Transport · {name}" for kind, _, name in TRANSPORT_BUTTONS},
}


def describe(action):
    if action is None:
        return "not assigned"
    if action.kind == "midi":
        return f"MIDI CC {action.cc}, {action.mode}"
    if action.kind == "media":
        return MEDIA_ACTIONS[action.media][0]
    if action.kind == "plugin":
        return f"plugin {action.plugin} → {action.target}"
    return f"runs: {action.command}"


def plugin_combo():
    """Installed plugins to pick from; editable, since one may be set up before it is installed."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(sorted(plugins.installed()))
    combo.setCurrentIndex(-1)
    combo.lineEdit().setPlaceholderText("e.g. hrdctl")
    return combo


class PluginsDialog(QDialog):
    """Turn installed plugins on and off. Each change is written at once and
    a running background bridge picks it up within a second."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("Plugins")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        available = plugins.installed()
        try:
            settings = plugins.load_settings(path)
        except (OSError, ValueError) as error:
            settings = None
            self.error.setText(f"Cannot read {path}: {error}")
        names = sorted(set(available) | set(settings or {}))
        if not names:
            layout.addWidget(label("No plugins are installed.", "muted"))
        for name in names:
            entry = available.get(name)
            source = f"{entry.dist.name} {entry.dist.version}" if entry is not None and entry.dist else "not installed"
            box = QCheckBox(f"{name}   ·   {source}")
            box.setChecked(bool(settings and name in settings and settings[name].enabled))
            box.setEnabled(settings is not None and (entry is not None or box.isChecked()))
            box.toggled.connect(lambda checked, name=name, box=box: self.toggle(name, checked, box))
            layout.addWidget(box)
        note = label(
            f"Changes take effect in the running background bridge within a second. "
            f"Plugin settings (host, port, ...) live in {path}. "
            f"If a plugin misbehaves, untick it here, or start the bridge with --no-plugins.", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def toggle(self, name, enabled, box):
        try:
            plugins.set_enabled(self.path, name, enabled)
        except (OSError, ValueError) as error:
            self.error.setText(f"Could not change {name}: {error}")
            box.blockSignals(True)
            box.setChecked(not enabled)
            box.blockSignals(False)
            return
        self.error.setText("")

STYLE = """
QWidget { background: #171b21; color: #e4e9ee; font-family: 'DejaVu Sans'; font-size: 13px; }
QLabel#eyebrow { color: #50d9bb; font-size: 11px; font-weight: bold; }
QLabel#title { font-size: 26px; font-weight: bold; }
QLabel#muted { color: #9ba8b6; }
QLabel#status { color: #e6bc73; background: #302b23; border-radius: 6px; padding: 9px; }
QFrame#strip { background: #20262e; border: 1px solid #35404c; border-radius: 9px; }
QFrame#strip[selected="true"] { border: 2px solid #50d9bb; background: #23332f; }
QFrame#strip QLabel { background: transparent; border: none; }
QFrame#strip QPushButton { padding: 6px 3px; font-size: 11px; }
QPushButton#hardware { padding: 2px; font-size: 12px; font-weight: bold; color: #b8c5d0; }
QPushButton#hardware[mapped="true"] { color: #102c25; background: #50d9bb; }
QPushButton#hardware[selected="true"] { border: 2px solid #e6bc73; }
QLabel#indicator { background: #10171d; border: 1px solid #45505b; border-radius: 3px; }
QLabel#number { font-size: 22px; font-weight: bold; color: #a6b4c3; }
QPushButton { background: #2d3642; border: 1px solid #465465; border-radius: 5px; padding: 9px 12px; }
QPushButton:hover { background: #394958; border-color: #50d9bb; }
QPushButton:focus { border: 2px solid #50d9bb; }
QPushButton#primary { background: #50d9bb; color: #102c25; font-weight: bold; }
QPushButton:disabled { color: #687582; background: #232a32; border-color: #35404c; }
QLineEdit, QSpinBox, QComboBox { background: #10151b; border: 1px solid #465465; border-radius: 4px; padding: 8px; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #50d9bb; }
QLabel#error { color: #ffaba5; }
QScrollArea { border: none; }
"""


def label(text, name=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    return widget


def hardware_button(text, description):
    """Reference controls remain visible without implying implemented actions."""
    button = QPushButton(text)
    button.setObjectName("hardware")
    button.setAccessibleName(description)
    button.setToolTip(f"{description} · not implemented yet")
    button.setEnabled(False)
    return button


def key_button(text, key, callback):
    """A physical button that can be given an action: opens the key editor."""
    button = QPushButton(text)
    button.setObjectName("hardware")
    button.setAccessibleName(f"Edit key {KEY_NAMES[key]}")
    button.clicked.connect(lambda checked=False: callback(key))
    return button


def refresh_key_button(button, key, action, selected):
    button.setProperty("mapped", action is not None)
    button.setProperty("selected", selected)
    button.style().unpolish(button)
    button.style().polish(button)
    button.setToolTip(f"{KEY_NAMES[key]} · note {KEY_NOTES[key]} · {describe(action)}\nClick to edit what this key does")


class Control(QAbstractButton):
    def __init__(self, kind, strip, callback):
        super().__init__()
        self.kind = kind
        self.setMinimumSize(64, 80 if kind == "pan" else 205)
        if kind == "pan":
            self.setFixedHeight(80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"Map strip {strip} {kind}")
        self.setToolTip(f"Click to map strip {strip} {kind}; no MIDI is sent")
        self.clicked.connect(callback)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.width() / 2
        accent = QColor("#50d9bb" if self.underMouse() or self.hasFocus() else "#8c9dac")
        if self.kind == "pan":
            painter.setPen(QPen(QColor("#43505e"), 3))
            painter.drawArc(QRectF(center - 29, 8, 58, 58), -45 * 16, 270 * 16)
            painter.setBrush(QColor("#11171e"))
            painter.setPen(QPen(accent, 2))
            painter.drawEllipse(QRectF(center - 21, 16, 42, 42))
            painter.setPen(QPen(accent, 3))
            painter.drawLine(int(center), 19, int(center), 31)
        else:
            bottom = self.height() - 18
            painter.setPen(QPen(QColor("#4c5864"), 1))
            for index in range(11):
                y = int(15 + (bottom - 15) * index / 10)
                painter.drawLine(int(center - 30), y, int(center - 19), y)
                painter.drawLine(int(center + 19), y, int(center + 30), y)
            painter.setPen(QPen(QColor("#0c1016"), 7))
            painter.drawLine(int(center), 12, int(center), bottom)
            y = int(self.height() * .57)
            painter.setBrush(QColor("#a5afb8"))
            painter.setPen(QPen(accent, 1))
            painter.drawRoundedRect(QRectF(center - 21, y - 18, 42, 36), 4, 4)
            painter.setPen(QPen(QColor("#27333d"), 3))
            painter.drawLine(int(center - 17), y, int(center + 17), y)
        painter.end()


class Strip(QFrame):
    def __init__(self, number, callback, key_callback):
        super().__init__()
        self.setObjectName("strip")
        self.setMinimumWidth(108)
        self.setMaximumWidth(170)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 13, 8, 13)
        header = label(f"{number:02}", "number")
        header.setMinimumHeight(30)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        # The encoder and fader share an axis; four buttons sit to their right.
        encoder_row = QHBoxLayout()
        encoder_row.setSpacing(4)
        encoder_row.addWidget(Control("pan", number, callback), 1)
        encoder_row.addSpacing(28)
        layout.addLayout(encoder_row)
        indicator_row = QHBoxLayout()
        indicator = label("", "indicator")
        indicator.setFixedSize(22, 6)
        indicator.setStyleSheet("background: #10171d; border: 1px solid #45505b; border-radius: 3px;")
        indicator.setToolTip("Hardware indicator · inactive in this preview")
        indicator_row.addWidget(indicator, 1, Qt.AlignmentFlag.AlignHCenter)
        indicator_row.addSpacing(32)
        layout.addLayout(indicator_row)
        controls = QHBoxLayout()
        controls.setSpacing(4)
        controls.addWidget(Control("volume", number, callback), 1)
        buttons = QVBoxLayout()
        buttons.setContentsMargins(0, 12, 0, 18)
        self.key_buttons = {}
        for index, (kind, text, _) in enumerate(STRIP_KEY_BUTTONS):
            if index:
                buttons.addStretch()
            key = f"strip{number}.{kind}"
            button = key_button(text, key, key_callback)
            self.key_buttons[key] = button
            button.setFixedSize(28, 30)
            buttons.addWidget(button)
        controls.addLayout(buttons)
        layout.addLayout(controls, 1)
        self.mapping_label = label("", "muted")
        self.mapping_label.setMinimumHeight(22)
        self.mapping_label.setStyleSheet("font-size: 11px;")
        self.mapping_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.mapping_label)
        self.button = QPushButton("Unassigned")
        self.button.setMinimumHeight(44)
        self.button.clicked.connect(callback)
        self.button.setAccessibleName(f"Map strip {number}")
        layout.addWidget(self.button)

    def refresh(self, mapping, selected):
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
        used = mapping.assigned or bool(mapping.plugin)
        self.button.setText(mapping.name if used else "Unassigned")
        self.button.setToolTip(mapping.name if used else "Click to assign a mixer channel")
        if mapping.plugin:
            self.mapping_label.setText(f"{mapping.plugin} · {mapping.target}")
        else:
            self.mapping_label.setText(f"VOL {mapping.volume_cc} · PAN {mapping.pan_cc}" if mapping.assigned else "—  ·  —")


class Window(QMainWindow):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.selected = 0
        self.selected_key = None
        self.loading = False
        self.draft_dirty = False
        self.dirty = False
        self.load_failed = False
        self.setWindowTitle("SMC Bridge — Channel mapping")
        self.resize(1380, 800)
        self.setStyleSheet(STYLE)
        initial_error = None
        try:
            self.config = load(path)
        except (OSError, ValueError) as error:
            self.config = Config()
            self.load_failed = True
            initial_error = f"Could not load {path}: {error}. Fix the file and reload; saving is disabled to protect it."
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 18)
        outer.setSpacing(12)
        outer.addWidget(label("SMC / MIXER BRIDGE", "eyebrow"))
        top = QHBoxLayout()
        top.addWidget(label("Your surface. Your channels.", "title"))
        top.addStretch()
        self.plugins_button = QPushButton("Plugins…")
        self.plugins_button.setToolTip("Turn plugins on and off")
        self.plugins_button.clicked.connect(lambda: PluginsDialog(plugins.settings_path(self.path), self).exec())
        top.addWidget(self.plugins_button)
        self.reload_button = QPushButton("Reload saved")
        self.reload_button.clicked.connect(self.reload)
        top.addWidget(self.reload_button)
        self.save_button = QPushButton("Save mappings")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        top.addWidget(self.save_button)
        outer.addLayout(top)
        outer.addWidget(label("Select a fader, encoder or channel label to map its strip, or any button to choose what it does.", "muted"))
        outer.addWidget(label("MAPPING EDITOR   ·   This window edits mappings only; live MIDI runs in the separate background daemon (--headless).", "status"))
        body = QHBoxLayout()
        surface = QWidget()
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(14)
        strips_layout = QHBoxLayout()
        surface_layout.addLayout(strips_layout, 1)
        strips_layout.setContentsMargins(0, 0, 0, 0)
        strips_layout.setSpacing(8)
        self.strips = []
        for index in range(8):
            strip = Strip(
                index + 1,
                lambda checked=False, index=index: self.select(index),
                self.select_key,
            )
            self.strips.append(strip)
            strips_layout.addWidget(strip)
        utilities = QVBoxLayout()
        utilities.setSpacing(12)
        utilities.addSpacing(48)
        for text, description in (("BT", "Bluetooth"), ("SHIFT", "Shift modifier")):
            button = hardware_button(text, description)
            button.setFixedSize(48, 24)
            utilities.addWidget(button)
        utilities.addStretch()
        strips_layout.addLayout(utilities)

        transport = QHBoxLayout()
        transport.setContentsMargins(16, 0, 16, 0)
        transport.setSpacing(10)
        self.key_buttons = {key: button for strip in self.strips for key, button in strip.key_buttons.items()}
        for kind, symbol, _ in TRANSPORT_BUTTONS:
            key = f"transport.{kind}"
            button = key_button(symbol, key, self.select_key)
            button.setMinimumSize(44, 28)
            self.key_buttons[key] = button
            transport.addWidget(button, 1)
        surface_layout.addLayout(transport)
        legend = label("M  Mute    S  Solo    R / □  Strip buttons    ·    Highlighted buttons have an action    ·    BT and SHIFT send no MIDI", "muted")
        legend.setWordWrap(True)
        surface_layout.addWidget(legend)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(surface)
        body.addWidget(scroll, 1)
        editor = QWidget()
        editor.setFixedWidth(270)
        panel = QVBoxLayout(editor)
        panel.setContentsMargins(16, 0, 0, 0)
        self.editor_title = label("", "title")
        panel.addWidget(self.editor_title)
        self.input_hint = label("", "muted")
        self.input_hint.setWordWrap(True)
        panel.addWidget(self.input_hint)
        self.pages = QStackedWidget()
        strip_page = QWidget()
        strip_layout = QVBoxLayout(strip_page)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox("Assign this strip")
        strip_layout.addWidget(self.enabled)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Desk Mic")
        self.name.setMaxLength(80)
        self.volume = QSpinBox()
        self.pan = QSpinBox()
        self.key_cc = QSpinBox()
        for spin in (self.volume, self.pan, self.key_cc):
            spin.setRange(-1, 127)
            spin.setSpecialValueText("Choose CC")
        self.route = QComboBox()
        for value, text in ROUTE_CHOICES:
            self.route.addItem(text, value)
        self.strip_plugin = plugin_combo()
        self.strip_target = QLineEdit()
        self.strip_target.setPlaceholderText("e.g. vfo_a")
        self.strip_form = QFormLayout()
        self.strip_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.strip_form.addRow("Channel name", self.name)
        self.strip_form.addRow("Send to", self.route)
        self.strip_form.addRow("Volume CC", self.volume)
        self.strip_form.addRow("Pan CC", self.pan)
        self.strip_form.addRow("Plugin", self.strip_plugin)
        self.strip_form.addRow("Plugin target", self.strip_target)
        strip_layout.addLayout(self.strip_form)
        self.strip_note = label("", "muted")
        self.strip_note.setWordWrap(True)
        strip_layout.addWidget(self.strip_note)
        strip_layout.addStretch()
        self.pages.addWidget(strip_page)

        key_page = QWidget()
        self.key_form = QFormLayout(key_page)
        self.key_form.setContentsMargins(0, 0, 0, 0)
        self.key_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.action = QComboBox()
        for value, text in ACTION_CHOICES:
            self.action.addItem(text, value)
        self.mode = QComboBox()
        for value, text in MODE_CHOICES:
            self.mode.addItem(text, value)
        self.media = QComboBox()
        for value, (text, _) in MEDIA_ACTIONS.items():
            self.media.addItem(text, value)
        self.command = QLineEdit()
        self.command.setPlaceholderText("e.g. notify-send 'SMC' 'Hello'")
        self.key_plugin = plugin_combo()
        self.key_target = QLineEdit()
        self.key_target.setPlaceholderText("e.g. ptt")
        self.key_form.addRow("When pressed", self.action)
        self.key_form.addRow("CC sent to jack_mixer", self.key_cc)
        self.key_form.addRow("Behavior", self.mode)
        self.key_form.addRow("Media command", self.media)
        self.key_form.addRow("Shell command", self.command)
        self.key_form.addRow("Plugin", self.key_plugin)
        self.key_form.addRow("Plugin target", self.key_target)
        self.key_note = label("", "muted")
        self.key_note.setWordWrap(True)
        self.key_form.addRow(self.key_note)
        self.pages.addWidget(key_page)
        panel.addWidget(self.pages)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        panel.addWidget(self.error)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply)
        panel.addWidget(self.apply_button)
        panel.addStretch()
        panel.addWidget(label("8 STRIPS / NO BANKING", "eyebrow"))
        body.addWidget(editor)
        outer.addLayout(body, 1)
        self.feedback = label("", "muted")
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        path_label = label(f"Configuration: {path}", "muted")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        outer.addWidget(path_label)
        self.enabled.toggled.connect(self.edited)
        self.name.textChanged.connect(self.edited)
        self.volume.valueChanged.connect(self.edited)
        self.pan.valueChanged.connect(self.edited)
        for combo in (self.action, self.mode, self.media, self.route):
            combo.currentIndexChanged.connect(self.edited)
        for combo in (self.strip_plugin, self.key_plugin):
            combo.currentTextChanged.connect(self.edited)
        self.key_cc.valueChanged.connect(self.edited)
        for line in (self.command, self.strip_target, self.key_target):
            line.textChanged.connect(self.edited)
        self.populate()
        self.refresh()
        if initial_error:
            self.feedback.setText(initial_error)
        else:
            self.feedback.setText("Saved mappings loaded." if path.exists() else "Start by selecting a strip and assigning its mixer channel.")

    def edited(self, *_):
        for widget in (self.name, self.route, self.volume, self.pan, self.strip_plugin, self.strip_target):
            widget.setEnabled(self.enabled.isChecked())
        to_plugin = self.route.currentData() == "plugin"
        for widget, shown in ((self.volume, not to_plugin), (self.pan, not to_plugin), (self.strip_plugin, to_plugin), (self.strip_target, to_plugin)):
            self.strip_form.setRowVisible(widget, shown)
        self.strip_note.setText(
            "The fader (as a 0-100% level) and the encoder (as steps) go to the plugin target; what the target means is up to the plugin. Turn plugins on with Plugins…."
            if to_plugin else
            "Use the CC numbers assigned to this channel in jack_mixer. Names are labels, not automatic discovery. Mute and Solo are set on their own buttons."
        )
        kind = self.action.currentData()
        for widget, kinds in (
            (self.key_cc, ("midi",)), (self.mode, ("midi",)), (self.media, ("media",)), (self.command, ("command",)),
            (self.key_plugin, ("plugin",)), (self.key_target, ("plugin",)),
        ):
            self.key_form.setRowVisible(widget, kind in kinds)
        self.key_note.setText({
            "": "This key does nothing.",
            "midi": "Toggle lights the key's LED while on and follows jack_mixer's feedback on the same CC, like Mute and Solo.",
            "media": "Sent to the active media player (MPRIS), like a keyboard media key.",
            "command": "Runs with /bin/sh in the background daemon, as you, once per press.",
            "plugin": "Sent to the plugin on press and on release (so a plugin can do push-to-talk). Turn plugins on with Plugins….",
        }[kind])
        if not self.loading:
            self.draft_dirty = True
            self.error.setText("")
            self.refresh()

    def populate(self):
        self.loading = True
        if self.selected_key is None:
            mapping = self.config.mappings[self.selected]
            self.pages.setCurrentIndex(0)
            self.editor_title.setText(f"Strip {self.selected + 1:02}")
            self.input_hint.setText(
                f"Hardware input: pitch bend ch {self.selected}\n"
                f"Pan encoder: CC {16 + self.selected}, ch 0\n"
                f"(channel numbers are zero-based)"
            )
            self.enabled.setChecked(mapping.assigned or bool(mapping.plugin))
            self.name.setText(mapping.name)
            self.route.setCurrentIndex(self.route.findData("plugin" if mapping.plugin else "mixer"))
            self.strip_plugin.setCurrentText(mapping.plugin)
            self.strip_target.setText(mapping.target)
            self.volume.setValue(mapping.volume_cc if mapping.volume_cc is not None else -1)
            self.pan.setValue(mapping.pan_cc if mapping.pan_cc is not None else -1)
        else:
            action = self.config.keys.get(self.selected_key) or KeyAction("")
            self.pages.setCurrentIndex(1)
            self.editor_title.setText(KEY_NAMES[self.selected_key].split(" · ")[1])
            self.input_hint.setText(f"{KEY_NAMES[self.selected_key]}\nHardware input: note {KEY_NOTES[self.selected_key]}, ch 0")
            self.action.setCurrentIndex(self.action.findData(action.kind))
            self.key_cc.setValue(action.cc if action.cc is not None else -1)
            self.mode.setCurrentIndex(self.mode.findData(action.mode))
            self.media.setCurrentIndex(max(0, self.media.findData(action.media)))
            self.command.setText(action.command)
            self.key_plugin.setCurrentText(action.plugin)
            self.key_target.setText(action.target)
        self.edited()
        self.error.setText("")
        self.loading = False
        self.draft_dirty = False

    def refresh(self):
        for index, strip in enumerate(self.strips):
            strip.refresh(self.config.mappings[index], self.selected_key is None and index == self.selected)
        for key, button in self.key_buttons.items():
            refresh_key_button(button, key, self.config.keys.get(key), key == self.selected_key)
        unsaved = self.dirty or self.draft_dirty
        self.setWindowTitle(f"SMC Bridge — Channel mapping{' *' if unsaved else ''}")
        self.save_button.setEnabled(not self.load_failed)
        self.apply_button.setEnabled(not self.load_failed)

    def _draft_key(self):
        kind = self.action.currentData()
        if kind == "midi":
            if self.key_cc.value() < 0:
                raise ValueError("Choose the CC number this key sends.")
            return KeyAction("midi", cc=self.key_cc.value(), mode=self.mode.currentData())
        if kind == "media":
            return KeyAction("media", media=self.media.currentData())
        if kind == "command":
            return KeyAction("command", command=self.command.text().strip())
        if kind == "plugin":
            return KeyAction("plugin", plugin=self.key_plugin.currentText().strip(), target=self.key_target.text().strip())
        return None

    def apply(self):
        candidate = Config(list(self.config.mappings), dict(self.config.keys))
        try:
            if self.selected_key is not None:
                action = self._draft_key()
                if action is None:
                    candidate.keys.pop(self.selected_key, None)
                else:
                    candidate.keys[self.selected_key] = action
            elif self.enabled.isChecked() and self.route.currentData() == "plugin":
                candidate.mappings[self.selected] = Mapping(
                    self.name.text().strip(),
                    plugin=self.strip_plugin.currentText().strip(), target=self.strip_target.text().strip(),
                )
            elif self.enabled.isChecked():
                if self.volume.value() < 0 or self.pan.value() < 0:
                    raise ValueError("Choose both volume and pan CC numbers.")
                candidate.mappings[self.selected] = Mapping(self.name.text().strip(), self.volume.value(), self.pan.value())
            else:
                candidate.mappings[self.selected] = Mapping()
            validate(candidate)
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self.dirty = self.dirty or candidate != self.config
        self.config = candidate
        self.draft_dirty = False
        self.error.setText("")
        self.feedback.setText("Change applied to this session. Save mappings to keep it after restart.")
        self.refresh()
        return True

    def _navigate(self, index, key):
        if (index, key) == (self.selected, self.selected_key):
            return True
        if self.draft_dirty and not self.apply():
            return False
        self.selected, self.selected_key = index, key
        self.populate()
        self.refresh()
        return True

    def select(self, index):
        self._navigate(index, None)

    def select_key(self, key):
        strip = int(key[5]) - 1 if key.startswith("strip") else self.selected
        if self._navigate(strip, key):
            self.action.setFocus(Qt.FocusReason.OtherFocusReason)

    def save(self):
        if self.load_failed or (self.draft_dirty and not self.apply()):
            return False
        try:
            save(self.path, self.config)
        except (OSError, ValueError) as error:
            self.feedback.setText(f"Could not save mappings: {error}")
            return False
        self.dirty = False
        self.feedback.setText("Mappings saved. MIDI remains inactive in this preview.")
        self.refresh()
        return True

    def reload(self):
        if self.dirty or self.draft_dirty:
            answer = QMessageBox.question(self, "Reload mappings?", "Discard unsaved changes and reload the saved mappings?", QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
        try:
            config = load(self.path)
        except (OSError, ValueError) as error:
            self.feedback.setText(f"Could not reload mappings: {error}")
            return
        self.config = config
        self.load_failed = False
        self.dirty = False
        self.populate()
        self.refresh()
        self.feedback.setText("Saved mappings restored." if self.path.exists() else "No saved file found; all strips are unassigned.")

    def closeEvent(self, event):
        if self.dirty or self.draft_dirty:
            answer = QMessageBox.question(self, "Unsaved mappings", "Save your mapping changes before closing?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel or (answer == QMessageBox.StandardButton.Save and not self.save()):
                event.ignore()
                return
        event.accept()
