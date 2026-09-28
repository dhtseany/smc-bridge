"""Persistent mappings and key actions, independent of the GUI and the MIDI transport."""
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import tempfile

from .actions import MEDIA_ACTIONS
from .bridge import KEY_NOTES

FORMAT_VERSION = 2
KEY_SECTION = "key:"
KEY_KINDS = ("midi", "media", "command")
MIDI_MODES = ("toggle", "momentary")


@dataclass(frozen=True)
class Mapping:
    name: str = ""
    volume_cc: int | None = None
    pan_cc: int | None = None

    @property
    def assigned(self):
        return self.volume_cc is not None and self.pan_cc is not None


@dataclass(frozen=True)
class KeyAction:
    """What one physical button does. Only the fields for `kind` are used."""
    kind: str
    cc: int | None = None
    mode: str = "toggle"
    media: str = ""
    command: str = ""


@dataclass
class Config:
    """Eight strip mappings plus the actions of the keys that have one, by key id."""
    mappings: list = field(default_factory=lambda: [Mapping() for _ in range(8)])
    keys: dict = field(default_factory=dict)


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "smc_bridge" / "mappings.ini"


def validate(config):
    mappings = config.mappings
    if len(mappings) != 8:
        raise ValueError("Exactly eight strip mappings are required.")
    used = {}

    def claim(cc, owner):
        if type(cc) is not int or not 0 <= cc <= 127:
            raise ValueError(f"{owner[0].upper()}{owner[1:]}: CC must be between 0 and 127.")
        if cc in used:
            raise ValueError(f"CC {cc} is already used by {used[cc]}. Choose a unique CC for each control.")
        used[cc] = owner

    for strip, mapping in enumerate(mappings, 1):
        if "\n" in mapping.name or "\r" in mapping.name:
            raise ValueError(f"Strip {strip}: channel names must fit on one line.")
        if (mapping.volume_cc is None) != (mapping.pan_cc is None):
            raise ValueError(f"Strip {strip}: assign both volume and pan CCs, or neither.")
        if mapping.assigned and not mapping.name.strip():
            raise ValueError(f"Strip {strip}: enter a channel name.")
        if mapping.assigned:
            claim(mapping.volume_cc, f"strip {strip} volume")
            claim(mapping.pan_cc, f"strip {strip} pan")
    for key, action in config.keys.items():
        if key not in KEY_NOTES:
            raise ValueError(f"Unknown key {key!r}.")
        if action.kind == "midi":
            if action.mode not in MIDI_MODES:
                raise ValueError(f"Key {key}: mode must be toggle or momentary.")
            claim(action.cc, f"key {key}")
        elif action.kind == "media":
            if action.media not in MEDIA_ACTIONS:
                raise ValueError(f"Key {key}: choose a media command.")
        elif action.kind == "command":
            if not action.command.strip():
                raise ValueError(f"Key {key}: enter a command.")
            if any(c in action.command for c in "\n\r\0"):
                raise ValueError(f"Key {key}: the command must fit on one line.")
        else:
            raise ValueError(f"Key {key}: unknown action {action.kind!r}.")


def _check_private(path):
    """Shell commands run as you, so only you may be able to change the file that defines them."""
    for target in (path, path.parent):
        info = target.stat()
        if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{target} must be owned by you and not writable by others to use command keys.")


def _cc(section, key):
    value = section.get(key, "").strip()
    return int(value) if value else None


def load(path):
    if not path.exists():
        return Config()
    parser = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        version = parser.getint("app", "format_version")
        if version not in (1, FORMAT_VERSION):
            raise ValueError("Unsupported configuration version.")
        strips = {f"strip{i}" for i in range(1, 9)}
        sections = set(parser.sections())
        key_sections = {name for name in sections if name.startswith(KEY_SECTION)} if version == FORMAT_VERSION else set()
        if sections - key_sections != {"app", *strips}:
            raise ValueError("Configuration must contain app and strip1 through strip8 sections.")
        config = Config([], {})
        for i in range(1, 9):
            section = parser[f"strip{i}"]
            config.mappings.append(Mapping(section.get("name", ""), _cc(section, "volume_cc"), _cc(section, "pan_cc")))
            if version == 1:
                # Format 1 kept mute/solo CCs on the strip; they are now
                # ordinary toggle MIDI keys.
                for control in ("mute", "solo"):
                    cc = _cc(section, f"{control}_cc")
                    if cc is not None:
                        config.keys[f"strip{i}.{control}"] = KeyAction("midi", cc)
        for name in sorted(key_sections):
            section = parser[name]
            kind = section.get("action", "").strip()
            config.keys[name[len(KEY_SECTION):]] = KeyAction(
                kind,
                cc=_cc(section, "cc") if kind == "midi" else None,
                mode=section.get("mode", "toggle").strip() if kind == "midi" else "toggle",
                media=section.get("media", "").strip() if kind == "media" else "",
                command=section.get("command", "") if kind == "command" else "",
            )
        validate(config)
        if any(action.kind == "command" for action in config.keys.values()):
            _check_private(path)
        return config
    except (ConfigError, KeyError) as error:
        raise ValueError(f"Invalid configuration: {error}") from error


def save(path, config):
    validate(config)
    parser = ConfigParser(interpolation=None)
    parser["app"] = {"format_version": str(FORMAT_VERSION)}
    for i, mapping in enumerate(config.mappings, 1):
        parser[f"strip{i}"] = {
            "name": mapping.name,
            "volume_cc": "" if mapping.volume_cc is None else str(mapping.volume_cc),
            "pan_cc": "" if mapping.pan_cc is None else str(mapping.pan_cc),
        }
    for key in KEY_NOTES:
        action = config.keys.get(key)
        if action is None:
            continue
        section = {"action": action.kind}
        if action.kind == "midi":
            section.update(cc=str(action.cc), mode=action.mode)
        elif action.kind == "media":
            section["media"] = action.media
        else:
            section["command"] = action.command
        parser[KEY_SECTION + key] = section
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            parser.write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
