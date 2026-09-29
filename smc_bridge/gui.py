"""Qt mapping surface. Visual controls edit mappings; they do not send MIDI."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from .actions import MEDIA_ACTIONS
from .bridge import KEY_NOTES
from .config import CCConflict, Config, KeyAction, Mapping, Route, STRIP_CONTROLS, load, save, validate
from . import plugins

ACTION_CHOICES = (("", "Nothing"), ("midi", "MIDI CC to jack_mixer"), ("media", "Media command"), ("command", "Shell command"), ("plugin", "Plugin"))
ROUTE_CHOICES = (("", "Nothing"), ("midi", "jack_mixer CC"), ("plugin", "Plugin"))
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


def control_name(control):
    """'Strip 02 · Fader' for a strip's fader or encoder id, else the key's name."""
    strip, _, part = control.partition(".")
    if part in STRIP_CONTROLS:
        return f"Strip {int(strip[len('strip'):]):02} · {part.capitalize()}"
    return KEY_NAMES[control]


def describe_route(route, short=False):
    if route is None:
        return "—" if short else "nothing"
    if route.kind == "midi":
        return f"CC {route.cc}" if short else f"jack_mixer CC {route.cc}"
    return route.plugin if short else f"plugin {route.plugin} → {route.target}"


def plugin_combo():
    """Installed plugins to pick from; editable, since one may be set up before it is installed."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(sorted(plugins.installed()))
    combo.setCurrentIndex(-1)
    combo.lineEdit().setPlaceholderText("e.g. hrdctl")
    return combo


class RouteEditor(QWidget):
    """Where one fader or encoder goes: nothing, a jack_mixer CC, or a plugin target."""

    def __init__(self, title, control, target_placeholder):
        super().__init__()
        self.control = control
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 8, 0, 0)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.kind = QComboBox()
        for value, text in ROUTE_CHOICES:
            self.kind.addItem(text, value)
        self.kind.setAccessibleName(f"{title} sends to")
        self.cc = QSpinBox()
        self.cc.setRange(-1, 127)
        self.cc.setSpecialValueText("Choose CC")
        self.plugin = plugin_combo()
        self.target = QLineEdit()
        self.target.setPlaceholderText(target_placeholder)
        self.form.addRow(label(title.upper(), "eyebrow"))
        self.form.addRow("Sends to", self.kind)
        self.form.addRow("CC sent to jack_mixer", self.cc)
        self.form.addRow("Plugin", self.plugin)
        self.form.addRow("Plugin target", self.target)

    def connect(self, slot):
        self.kind.currentIndexChanged.connect(slot)
        self.cc.valueChanged.connect(slot)
        self.plugin.currentTextChanged.connect(slot)
        self.target.textChanged.connect(slot)

    def update_rows(self):
        kind = self.kind.currentData()
        for widget, shown in ((self.cc, kind == "midi"), (self.plugin, kind == "plugin"), (self.target, kind == "plugin")):
            self.form.setRowVisible(widget, shown)

    def set_route(self, route):
        self.kind.setCurrentIndex(self.kind.findData("" if route is None else route.kind))
        self.cc.setValue(route.cc if route is not None and route.cc is not None else -1)
        self.plugin.setCurrentText(route.plugin if route is not None else "")
        self.target.setText(route.target if route is not None else "")

    def route(self):
        kind = self.kind.currentData()
        if kind == "midi":
            if self.cc.value() < 0:
                raise ValueError(f"Choose the CC number the {self.control} sends.")
            return Route.midi(self.cc.value())
        if kind == "plugin":
            return Route.to_plugin(self.plugin.currentText().strip(), self.target.text().strip())
        return None


KEY_NOTE_TEXT = {
    "": "This key does nothing.",
    "midi": "Toggle lights the key's LED while on and follows jack_mixer's feedback on the same CC, like Mute and Solo.",
    "media": "Sent to the active media player (MPRIS), like a keyboard media key.",
    "command": "Runs with /bin/sh in the background daemon, as you, once per press.",
    "plugin": "Sent to the plugin on press and on release (so a plugin can do push-to-talk). Turn plugins on with Plugins….",
}


class KeyEditor(QWidget):
    """What one physical button does."""

    def __init__(self):
        super().__init__()
        self.key = None
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 8, 0, 0)
        self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.title = label("", "eyebrow")
        self.hint = label("", "muted")
        self.action = QComboBox()
        for value, text in ACTION_CHOICES:
            self.action.addItem(text, value)
        self.cc = QSpinBox()
        self.cc.setRange(-1, 127)
        self.cc.setSpecialValueText("Choose CC")
        self.mode = QComboBox()
        for value, text in MODE_CHOICES:
            self.mode.addItem(text, value)
        self.media = QComboBox()
        for value, (text, _) in MEDIA_ACTIONS.items():
            self.media.addItem(text, value)
        self.command = QLineEdit()
        self.command.setPlaceholderText("e.g. notify-send 'SMC' 'Hello'")
        self.plugin = plugin_combo()
        self.target = QLineEdit()
        self.target.setPlaceholderText("e.g. ptt")
        self.note = label("", "muted")
        self.note.setWordWrap(True)
        self.form.addRow(self.title)
        self.form.addRow(self.hint)
        self.form.addRow("When pressed", self.action)
        self.form.addRow("CC sent to jack_mixer", self.cc)
        self.form.addRow("Behavior", self.mode)
        self.form.addRow("Media command", self.media)
        self.form.addRow("Shell command", self.command)
        self.form.addRow("Plugin", self.plugin)
        self.form.addRow("Plugin target", self.target)
        self.form.addRow(self.note)

    def connect(self, slot):
        for combo in (self.action, self.mode, self.media):
            combo.currentIndexChanged.connect(slot)
        self.cc.valueChanged.connect(slot)
        self.plugin.currentTextChanged.connect(slot)
        self.command.textChanged.connect(slot)
        self.target.textChanged.connect(slot)

    def update_rows(self):
        kind = self.action.currentData()
        for widget, kinds in (
            (self.cc, ("midi",)), (self.mode, ("midi",)), (self.media, ("media",)), (self.command, ("command",)),
            (self.plugin, ("plugin",)), (self.target, ("plugin",)),
        ):
            self.form.setRowVisible(widget, kind in kinds)
        self.note.setText(KEY_NOTE_TEXT[kind])

    def set_key(self, key, action, title):
        self.key = key
        self.title.setText(title.upper())
        self.hint.setText(f"Input: note {KEY_NOTES[key]}, ch 0")
        self.action.setAccessibleName(f"{KEY_NAMES[key]}: when pressed")
        action = action or KeyAction("")
        self.action.setCurrentIndex(self.action.findData(action.kind))
        self.cc.setValue(action.cc if action.cc is not None else -1)
        self.mode.setCurrentIndex(self.mode.findData(action.mode))
        self.media.setCurrentIndex(max(0, self.media.findData(action.media)))
        self.command.setText(action.command)
        self.plugin.setCurrentText(action.plugin)
        self.target.setText(action.target)

    def draft(self):
        """The KeyAction being edited, or None for Nothing."""
        kind = self.action.currentData()
        if kind == "midi":
            if self.cc.value() < 0:
                raise ValueError(f"{KEY_NAMES[self.key]}: choose the CC number this key sends.")
            return KeyAction("midi", cc=self.cc.value(), mode=self.mode.currentData())
        if kind == "media":
            return KeyAction("media", media=self.media.currentData())
        if kind == "command":
            return KeyAction("command", command=self.command.text().strip())
        if kind == "plugin":
            return KeyAction("plugin", plugin=self.plugin.currentText().strip(), target=self.target.text().strip())
        return None


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
        name = "encoder" if kind == "pan" else "fader"
        self.setAccessibleName(f"Map strip {strip} {name}")
        self.setToolTip(f"Click to map strip {strip}'s {name}; no MIDI is sent")
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
    def __init__(self, number, callback, key_callback, control_callback):
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
        encoder_row.addWidget(Control("pan", number, lambda checked=False: control_callback("encoder")), 1)
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
        controls.addWidget(Control("volume", number, lambda checked=False: control_callback("fader")), 1)
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
        # A named strip keeps its name with nothing routed (its buttons may
        # still do things, or a replace took its only route).
        self.button.setText(mapping.name or "Unassigned")
        self.button.setToolTip(mapping.name or "Click to map this strip's fader and encoder")
        self.mapping_label.setText(f"{describe_route(mapping.fader, True)} · {describe_route(mapping.encoder, True)}")
        self.mapping_label.setToolTip(f"Fader: {describe_route(mapping.fader)}\nEncoder: {describe_route(mapping.encoder)}")


class Window(QMainWindow):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.selected = 0
        # A strip view (selected_key None or one of the strip's buttons)
        # shows the strip's fader, encoder and all four buttons; a transport
        # key shows just itself. selected_key is what was clicked.
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
        self.reset_button = QPushButton("Reset all…")
        self.reset_button.setToolTip("Unset every fader, encoder and button")
        self.reset_button.clicked.connect(self.reset_all)
        top.addWidget(self.reset_button)
        self.reload_button = QPushButton("Reload saved")
        self.reload_button.clicked.connect(self.reload)
        top.addWidget(self.reload_button)
        self.save_button = QPushButton("Save mappings")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        top.addWidget(self.save_button)
        outer.addLayout(top)
        outer.addWidget(label("Select a fader, encoder or button to choose what it does. Each one is set on its own.", "muted"))
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
                lambda control, index=index: self.select_control(index, control),
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
        # Fader and encoder each have their own fields, which outgrow short windows.
        self.editor_scroll = QScrollArea()
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor_scroll.setFixedWidth(270 + self.editor_scroll.verticalScrollBar().sizeHint().width())
        self.editor_scroll.setWidget(editor)
        # The error and Apply stay in view below the scrolling fields.
        editor_column = QWidget()
        editor_column.setFixedWidth(self.editor_scroll.width())
        column = QVBoxLayout(editor_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.editor_scroll, 1)
        pinned = QVBoxLayout()
        pinned.setContentsMargins(16, 0, 0, 0)
        column.addLayout(pinned)
        self.editor_title = label("", "title")
        panel.addWidget(self.editor_title)
        self.input_hint = label("", "muted")
        self.input_hint.setWordWrap(True)
        panel.addWidget(self.input_hint)
        self.strip_section = QWidget()
        strip_layout = QVBoxLayout(self.strip_section)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Desk Mic")
        self.name.setMaxLength(80)
        name_form = QFormLayout()
        name_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        name_form.addRow("Channel name", self.name)
        strip_layout.addLayout(name_form)
        self.fader = RouteEditor("Fader", "fader", "e.g. af_gain")
        self.encoder = RouteEditor("Encoder", "encoder", "e.g. vfo_a")
        strip_layout.addWidget(self.fader)
        strip_layout.addWidget(self.encoder)
        self.strip_note = label(
            "To jack_mixer the encoder works like pan, starting centered. To a plugin the fader sends a "
            "0-100% level and the encoder steps; the plugin decides what a target means.", "muted")
        self.strip_note.setWordWrap(True)
        strip_layout.addWidget(self.strip_note)
        self.strip_keys = {}
        for kind, _, _ in STRIP_KEY_BUTTONS:
            self.strip_keys[kind] = KeyEditor()
            strip_layout.addWidget(self.strip_keys[kind])
        panel.addWidget(self.strip_section)
        self.transport_key = KeyEditor()
        panel.addWidget(self.transport_key)
        panel.addStretch()
        self.error = label("", "error")
        self.error.setWordWrap(True)
        pinned.addWidget(self.error)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply)
        pinned.addWidget(self.apply_button)
        pinned.addWidget(label("8 STRIPS / NO BANKING", "eyebrow"))
        body.addWidget(editor_column)
        outer.addLayout(body, 1)
        self.feedback = label("", "muted")
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        path_label = label(f"Configuration: {path}", "muted")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setWordWrap(True)
        outer.addWidget(path_label)
        self.name.textChanged.connect(self.edited)
        self.fader.connect(self.edited)
        self.encoder.connect(self.edited)
        for editor in self._key_editors():
            editor.connect(self.edited)
        self.populate()
        self.refresh()
        if initial_error:
            self.feedback.setText(initial_error)
        else:
            self.feedback.setText("Saved mappings loaded." if path.exists() else "Start by selecting a fader, encoder or button.")

    def _key_editors(self):
        return [*self.strip_keys.values(), self.transport_key]

    def edited(self, *_):
        self.fader.update_rows()
        self.encoder.update_rows()
        for editor in self._key_editors():
            editor.update_rows()
        if not self.loading:
            self.draft_dirty = True
            self.error.setText("")
            self.refresh()

    @property
    def strip_view(self):
        return self.selected_key is None or self.selected_key.startswith("strip")

    @property
    def key_editor(self):
        """The editor for the clicked key, or None if a strip was opened from its fader, encoder or label."""
        if self.selected_key is None:
            return None
        return self.strip_keys[self.selected_key.split(".")[1]] if self.strip_view else self.transport_key

    def _strip_key_ids(self):
        return {kind: f"strip{self.selected + 1}.{kind}" for kind in self.strip_keys}

    def populate(self):
        self.loading = True
        strip_view = self.strip_view
        self.strip_section.setVisible(strip_view)
        self.transport_key.setVisible(not strip_view)
        if strip_view:
            mapping = self.config.mappings[self.selected]
            self.editor_title.setText(f"Strip {self.selected + 1:02}")
            self.input_hint.setText(
                f"Fader input: pitch bend ch {self.selected}\n"
                f"Encoder input: CC {16 + self.selected}, ch 0\n"
                f"(channel numbers are zero-based)"
            )
            self.name.setText(mapping.name)
            self.fader.set_route(mapping.fader)
            self.encoder.set_route(mapping.encoder)
            for (kind, text, name), key in zip(STRIP_KEY_BUTTONS, self._strip_key_ids().values()):
                self.strip_keys[kind].set_key(key, self.config.keys.get(key), f"{text}  ·  {name}")
        else:
            name = KEY_NAMES[self.selected_key].split(" · ")[1]
            self.editor_title.setText(name)
            self.input_hint.setText(KEY_NAMES[self.selected_key])
            self.transport_key.set_key(self.selected_key, self.config.keys.get(self.selected_key), "Button")
        self.edited()
        self.error.setText("")
        self.loading = False
        self.draft_dirty = False

    def refresh(self):
        for index, strip in enumerate(self.strips):
            strip.refresh(self.config.mappings[index], self.strip_view and index == self.selected)
        for key, button in self.key_buttons.items():
            refresh_key_button(button, key, self.config.keys.get(key), key == self.selected_key)
        unsaved = self.dirty or self.draft_dirty
        self.setWindowTitle(f"SMC Bridge — Channel mapping{' *' if unsaved else ''}")
        self.save_button.setEnabled(not self.load_failed)
        self.apply_button.setEnabled(not self.load_failed)

    def _draft_controls(self):
        """Ids of the controls the editor is showing, as Config.without() takes them."""
        if not self.strip_view:
            return {self.selected_key}
        return {f"strip{self.selected + 1}.{control}" for control in STRIP_CONTROLS} | set(self._strip_key_ids().values())

    def confirm_replace(self, cc, other):
        answer = QMessageBox.question(
            self, "CC already in use",
            f"CC {cc} is already used by {control_name(other)}.\n\nReplace it? {control_name(other)} will be unset.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def apply(self):
        candidate = Config(list(self.config.mappings), dict(self.config.keys))
        replaced = []
        try:
            if self.strip_view:
                mapping = Mapping(self.name.text().strip(), self.fader.route(), self.encoder.route())
                candidate.mappings[self.selected] = mapping
                drafts = [(self.strip_keys[kind], key) for kind, key in self._strip_key_ids().items()]
            else:
                drafts = [(self.transport_key, self.selected_key)]
            for editor, key in drafts:
                action = editor.draft()
                if action is None:
                    candidate.keys.pop(key, None)
                else:
                    candidate.keys[key] = action
            while True:
                try:
                    validate(candidate)
                    break
                except CCConflict as conflict:
                    # Offer to take the CC from a control outside this draft;
                    # a clash within the draft is the user's to fix.
                    others = [c for c in (conflict.first, conflict.second) if c not in self._draft_controls()]
                    if len(others) != 1 or not self.confirm_replace(conflict.cc, others[0]):
                        raise
                    candidate = candidate.without(others[0])
                    replaced.append(control_name(others[0]))
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self.dirty = self.dirty or candidate != self.config
        self.config = candidate
        self.draft_dirty = False
        self.error.setText("")
        self.feedback.setText(
            (f"Unset {', '.join(replaced)}. " if replaced else "")
            + "Change applied to this session. Save mappings to keep it after restart."
        )
        self.refresh()
        return True

    def reset_all(self):
        answer = QMessageBox.question(
            self, "Reset all mappings?",
            "Unset every fader, encoder and button, and clear all channel names?\n\n"
            "Nothing is written until you click Save mappings; Reload saved undoes this.",
            QMessageBox.StandardButton.Reset | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Reset:
            return
        # A deliberate reset may replace a file that failed to load.
        self.dirty = self.dirty or self.load_failed or self.config != Config()
        self.config = Config()
        self.load_failed = False
        self.populate()
        self.refresh()
        self.feedback.setText("Everything is unset. Save mappings to keep it, or Reload saved to undo.")

    def _view(self, index, key):
        return ("strip", index) if key is None or key.startswith("strip") else ("key", key)

    def _navigate(self, index, key):
        if self._view(index, key) == self._view(self.selected, self.selected_key):
            # Same panel: the draft carries on; only the highlight moves.
            self.selected, self.selected_key = index, key
            self.refresh()
            return True
        if self.draft_dirty and not self.apply():
            return False
        self.selected, self.selected_key = index, key
        self.populate()
        self.refresh()
        return True

    def select(self, index):
        return self._navigate(index, None)

    def select_control(self, index, control):
        if self.select(index):
            self._focus(getattr(self, control).kind)

    def select_key(self, key):
        strip = int(key[5]) - 1 if key.startswith("strip") else self.selected
        if self._navigate(strip, key):
            self._focus(self.key_editor.action)

    def _focus(self, widget):
        """Focus `widget` and scroll the editor to it; a strip's buttons sit below its fader and encoder."""
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        QApplication.processEvents()
        self.editor_scroll.ensureWidgetVisible(widget, 0, 80)

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
        self.feedback.setText("Saved mappings restored." if self.path.exists() else "No saved file found; nothing is mapped.")

    def closeEvent(self, event):
        if self.dirty or self.draft_dirty:
            answer = QMessageBox.question(self, "Unsaved mappings", "Save your mapping changes before closing?", QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel or (answer == QMessageBox.StandardButton.Save and not self.save()):
                event.ignore()
                return
        event.accept()
