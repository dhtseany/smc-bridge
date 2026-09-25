"""Qt mapping surface. Visual controls edit mappings; they do not send MIDI."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from .config import Mapping, load, save, validate

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
QLabel#indicator { background: #10171d; border: 1px solid #45505b; border-radius: 3px; }
QLabel#number { font-size: 22px; font-weight: bold; color: #a6b4c3; }
QPushButton { background: #2d3642; border: 1px solid #465465; border-radius: 5px; padding: 9px 12px; }
QPushButton:hover { background: #394958; border-color: #50d9bb; }
QPushButton:focus { border: 2px solid #50d9bb; }
QPushButton#primary { background: #50d9bb; color: #102c25; font-weight: bold; }
QPushButton:disabled { color: #687582; background: #232a32; border-color: #35404c; }
QLineEdit, QSpinBox { background: #10151b; border: 1px solid #465465; border-radius: 4px; padding: 8px; }
QLineEdit:focus, QSpinBox:focus { border-color: #50d9bb; }
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


def mappable_button(text, description, callback):
    """Like hardware_button, but clickable: jumps the editor to this control's field."""
    button = QPushButton(text)
    button.setObjectName("hardware")
    button.setAccessibleName(description)
    button.setToolTip(f"{description} · click to edit its jack_mixer binding")
    button.clicked.connect(callback)
    return button


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
    def __init__(self, number, callback, field_callback):
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
        for index, (text, description, field) in enumerate((
            ("M", "Mute", "mute"), ("S", "Solo", "solo"),
            ("R", "R button (function and MIDI mapping unverified)", None),
            ("□", "Square button (function and MIDI mapping unverified)", None),
        )):
            if index:
                buttons.addStretch()
            if field:
                button = mappable_button(text, f"Strip {number}: {description}", lambda checked=False, field=field: field_callback(field))
                setattr(self, f"{field}_button", button)
            else:
                button = hardware_button(text, f"Strip {number}: {description}")
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
        self.button.setText(mapping.name if mapping.assigned else "Unassigned")
        self.button.setToolTip(mapping.name if mapping.assigned else "Click to assign a mixer channel")
        self.mapping_label.setText(f"VOL {mapping.volume_cc} · PAN {mapping.pan_cc}" if mapping.assigned else "—  ·  —")


class Window(QMainWindow):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.selected = 0
        self.loading = False
        self.draft_dirty = False
        self.dirty = False
        self.load_failed = False
        self.setWindowTitle("SMC Mixer — Channel mapping")
        self.resize(1380, 800)
        self.setStyleSheet(STYLE)
        initial_error = None
        try:
            self.mappings = load(path)
        except (OSError, ValueError) as error:
            self.mappings = [Mapping() for _ in range(8)]
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
        self.reload_button = QPushButton("Reload saved")
        self.reload_button.clicked.connect(self.reload)
        top.addWidget(self.reload_button)
        self.save_button = QPushButton("Save mappings")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        top.addWidget(self.save_button)
        outer.addLayout(top)
        outer.addWidget(label("Select a fader, encoder, channel label, M, or S to map its strip — clicking M or S jumps straight to that field.", "muted"))
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
                lambda field, index=index: self.select(index, focus=field),
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
        # Exact left-to-right symbol order from the supplied hardware photo.
        for symbol, description in (
            ("▶", "Play"), ("Ⅱ", "Pause"), ("●", "Record"),
            ("◀◀", "Rewind"), ("▶▶", "Fast forward"),
            ("≪", "Double chevron left"), ("≫", "Double chevron right"),
            ("△", "Up"), ("▽", "Down"), ("◁", "Left"), ("▷", "Right"),
        ):
            button = hardware_button(symbol, description)
            button.setMinimumSize(44, 28)
            transport.addWidget(button, 1)
        surface_layout.addLayout(transport)
        legend = label("M  Mute    S  Solo    R / □  Hardware buttons    ·    Additional buttons are preview-only", "muted")
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
        self.enabled = QCheckBox("Assign this strip")
        panel.addWidget(self.enabled)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Desk Mic")
        self.name.setMaxLength(80)
        self.volume = QSpinBox()
        self.pan = QSpinBox()
        self.mute = QSpinBox()
        self.solo = QSpinBox()
        for spin in (self.volume, self.pan, self.mute, self.solo):
            spin.setRange(-1, 127)
            spin.setSpecialValueText("Choose CC")
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.addRow("Mixer channel name", self.name)
        form.addRow("Volume CC", self.volume)
        form.addRow("Pan CC", self.pan)
        form.addRow("Mute CC (optional)", self.mute)
        form.addRow("Solo CC (optional)", self.solo)
        panel.addLayout(form)
        note = label("Use the CC numbers assigned to this channel in jack_mixer. Names are labels, not automatic discovery. Mute/Solo are optional.", "muted")
        note.setWordWrap(True)
        panel.addWidget(note)
        self.error = label("", "error")
        self.error.setWordWrap(True)
        panel.addWidget(self.error)
        self.apply_button = QPushButton("Apply strip mapping")
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
        self.mute.valueChanged.connect(self.edited)
        self.solo.valueChanged.connect(self.edited)
        self.populate()
        self.refresh()
        if initial_error:
            self.feedback.setText(initial_error)
        else:
            self.feedback.setText("Saved mappings loaded." if path.exists() else "Start by selecting a strip and assigning its mixer channel.")

    def edited(self, *_):
        for widget in (self.name, self.volume, self.pan, self.mute, self.solo):
            widget.setEnabled(self.enabled.isChecked())
        if not self.loading:
            self.draft_dirty = True
            self.error.setText("")
            self.refresh()

    def populate(self):
        self.loading = True
        mapping = self.mappings[self.selected]
        self.editor_title.setText(f"Strip {self.selected + 1:02}")
        self.input_hint.setText(
            f"Hardware input: pitch bend ch {self.selected}\n"
            f"Pan encoder: CC {16 + self.selected}, ch 0\n"
            f"Mute button: note {16 + self.selected}, ch 0\n"
            f"Solo button: note {8 + self.selected}, ch 0\n"
            f"(channel numbers are zero-based)"
        )
        self.enabled.setChecked(mapping.assigned)
        self.name.setText(mapping.name)
        self.volume.setValue(mapping.volume_cc if mapping.volume_cc is not None else -1)
        self.pan.setValue(mapping.pan_cc if mapping.pan_cc is not None else -1)
        self.mute.setValue(mapping.mute_cc if mapping.mute_cc is not None else -1)
        self.solo.setValue(mapping.solo_cc if mapping.solo_cc is not None else -1)
        self.edited()
        self.error.setText("")
        self.loading = False
        self.draft_dirty = False

    def refresh(self):
        for index, strip in enumerate(self.strips):
            strip.refresh(self.mappings[index], index == self.selected)
        unsaved = self.dirty or self.draft_dirty
        self.setWindowTitle(f"SMC Mixer — Channel mapping{' *' if unsaved else ''}")
        self.save_button.setEnabled(not self.load_failed)
        self.apply_button.setEnabled(not self.load_failed)

    def apply(self):
        candidate = list(self.mappings)
        if self.enabled.isChecked():
            if self.volume.value() < 0 or self.pan.value() < 0:
                self.error.setText("Choose both volume and pan CC numbers.")
                return False
            mute_cc = self.mute.value() if self.mute.value() >= 0 else None
            solo_cc = self.solo.value() if self.solo.value() >= 0 else None
            candidate[self.selected] = Mapping(self.name.text().strip(), self.volume.value(), self.pan.value(), mute_cc, solo_cc)
        else:
            candidate[self.selected] = Mapping()
        try:
            validate(candidate)
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self.dirty = self.dirty or candidate != self.mappings
        self.mappings = candidate
        self.draft_dirty = False
        self.error.setText("")
        self.feedback.setText("Mapping applied to this session. Save mappings to keep it after restart.")
        self.refresh()
        return True

    def select(self, index, focus=None):
        if index != self.selected:
            if self.draft_dirty and not self.apply():
                return
            self.selected = index
            self.populate()
            self.refresh()
        if focus:
            self.focus_field(focus)

    def focus_field(self, field):
        widget = {"mute": self.mute, "solo": self.solo}.get(field)
        if widget is not None:
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            widget.selectAll()

    def save(self):
        if self.load_failed or (self.draft_dirty and not self.apply()):
            return False
        try:
            save(self.path, self.mappings)
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
            mappings = load(self.path)
        except (OSError, ValueError) as error:
            self.feedback.setText(f"Could not reload mappings: {error}")
            return
        self.mappings = mappings
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
