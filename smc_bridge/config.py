"""Persistent mappings and key actions, independent of the GUI and the MIDI transport."""
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import stat
import tempfile

from .actions import MEDIA_ACTIONS
from .bridge import KEY_NOTES

FORMAT_VERSION = 4
KEY_SECTION = "key:"
KEY_KINDS = ("midi", "media", "command", "plugin")
ROUTE_KINDS = ("midi", "plugin")
STRIP_CONTROLS = ("fader", "encoder")
MIDI_MODES = ("toggle", "momentary")
PLUGIN_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


@dataclass(frozen=True)
class Route:
    """Where one fader or encoder goes: a jack_mixer CC ("midi") or a plugin target."""
    kind: str
    cc: int | None = None
    plugin: str = ""
    target: str = ""

    @classmethod
    def midi(cls, cc):
        return cls("midi", cc=cc)

    @classmethod
    def to_plugin(cls, plugin, target):
        return cls("plugin", plugin=plugin, target=target)


@dataclass(frozen=True)
class Mapping:
    """One strip: a label plus independent routes for its fader and its
    encoder. Either may be None, and it then does nothing."""
    name: str = ""
    fader: Route | None = None
    encoder: Route | None = None

    @property
    def used(self):
        return self.fader is not None or self.encoder is not None

    def cc(self, control):
        """The jack_mixer CC `control` ("fader" or "encoder") sends, or None."""
        route = getattr(self, control)
        return route.cc if route is not None and route.kind == "midi" else None

    def plugin_route(self, control):
        """The plugin Route of `control`, or None if it is not sent to a plugin."""
        route = getattr(self, control)
        return route if route is not None and route.kind == "plugin" else None


@dataclass(frozen=True)
class KeyAction:
    """What one physical button does. Only the fields for `kind` are used."""
    kind: str
    cc: int | None = None
    mode: str = "toggle"
    media: str = ""
    command: str = ""
    plugin: str = ""
    target: str = ""


@dataclass
class Config:
    """Eight strip mappings plus the actions of the keys that have one, by key id."""
    mappings: list = field(default_factory=lambda: [Mapping() for _ in range(8)])
    keys: dict = field(default_factory=dict)


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "smc_bridge" / "mappings.ini"


def _check_plugin_route(owner, plugin, target):
    if not PLUGIN_NAME.match(plugin):
        raise ValueError(f"{owner}: plugin names are lowercase letters, digits, - and _.")
    if not target.strip():
        raise ValueError(f"{owner}: enter the plugin target.")
    if any(c in target for c in "\n\r\0"):
        raise ValueError(f"{owner}: the plugin target must fit on one line.")


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
        if mapping.used and not mapping.name.strip():
            raise ValueError(f"Strip {strip}: enter a channel name.")
        for control in STRIP_CONTROLS:
            route = getattr(mapping, control)
            if route is None:
                continue
            if route.kind == "midi":
                claim(route.cc, f"strip {strip} {control}")
            elif route.kind == "plugin":
                _check_plugin_route(f"Strip {strip} {control}", route.plugin, route.target)
            else:
                raise ValueError(f"Strip {strip} {control}: unknown destination {route.kind!r}.")
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
        elif action.kind == "plugin":
            _check_plugin_route(f"Key {key}", action.plugin, action.target)
        else:
            raise ValueError(f"Key {key}: unknown action {action.kind!r}.")


def check_private(path, purpose):
    """Refuse a file (or its directory) that someone else could change.

    Used where the file decides what runs as you: shell commands, and which
    plugins load and with what settings.
    """
    for target in (path, path.parent):
        info = target.stat()
        if info.st_uid != os.getuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{target} must be owned by you and not writable by others {purpose}.")


def _cc(section, key):
    value = section.get(key, "").strip()
    return int(value) if value else None


def _load_strip(section, version):
    name = section.get("name", "")
    if version >= 4:
        routes = {}
        for control in STRIP_CONTROLS:
            kind = section.get(control, "").strip()
            routes[control] = Route(
                kind,
                cc=_cc(section, f"{control}_cc") if kind == "midi" else None,
                plugin=section.get(f"{control}_plugin", "").strip() if kind == "plugin" else "",
                target=section.get(f"{control}_target", "").strip() if kind == "plugin" else "",
            ) if kind else None
        return Mapping(name, **routes)
    # Formats 1-3: volume_cc and pan_cc went to jack_mixer together, or
    # plugin/target took both the fader and the encoder.
    plugin, target = section.get("plugin", "").strip(), section.get("target", "").strip()
    if plugin or target:
        return Mapping(name, Route.to_plugin(plugin, target), Route.to_plugin(plugin, target))
    volume, pan = _cc(section, "volume_cc"), _cc(section, "pan_cc")
    return Mapping(name, None if volume is None else Route.midi(volume), None if pan is None else Route.midi(pan))


def load(path):
    if not path.exists():
        return Config()
    parser = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        version = parser.getint("app", "format_version")
        if version not in (1, 2, 3, FORMAT_VERSION):
            raise ValueError("Unsupported configuration version.")
        strips = {f"strip{i}" for i in range(1, 9)}
        sections = set(parser.sections())
        key_sections = {name for name in sections if name.startswith(KEY_SECTION)} if version >= 2 else set()
        if sections - key_sections != {"app", *strips}:
            raise ValueError("Configuration must contain app and strip1 through strip8 sections.")
        config = Config([], {})
        for i in range(1, 9):
            section = parser[f"strip{i}"]
            config.mappings.append(_load_strip(section, version))
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
                plugin=section.get("plugin", "").strip() if kind == "plugin" else "",
                target=section.get("target", "").strip() if kind == "plugin" else "",
            )
        validate(config)
        if any(action.kind == "command" for action in config.keys.values()):
            check_private(path, "to use command keys")
        # A plugin route fires an action of a plugin you trusted (a radio's
        # push-to-talk, say), so it needs the same protection.
        if (any(m.plugin_route(c) for m in config.mappings for c in STRIP_CONTROLS)
                or any(a.kind == "plugin" for a in config.keys.values())):
            check_private(path, "to send controls to plugins")
        return config
    except (ConfigError, KeyError) as error:
        raise ValueError(f"Invalid configuration: {error}") from error


def save(path, config):
    validate(config)
    parser = ConfigParser(interpolation=None)
    parser["app"] = {"format_version": str(FORMAT_VERSION)}
    for i, mapping in enumerate(config.mappings, 1):
        section = {"name": mapping.name}
        for control in STRIP_CONTROLS:
            route = getattr(mapping, control)
            section[control] = "" if route is None else route.kind
            if route is None:
                continue
            if route.kind == "midi":
                section[f"{control}_cc"] = str(route.cc)
            else:
                section.update({f"{control}_plugin": route.plugin, f"{control}_target": route.target})
        parser[f"strip{i}"] = section
    for key in KEY_NOTES:
        action = config.keys.get(key)
        if action is None:
            continue
        section = {"action": action.kind}
        if action.kind == "midi":
            section.update(cc=str(action.cc), mode=action.mode)
        elif action.kind == "media":
            section["media"] = action.media
        elif action.kind == "plugin":
            section.update(plugin=action.plugin, target=action.target)
        else:
            section["command"] = action.command
        parser[KEY_SECTION + key] = section
    write_atomic(path, parser.write)


def write_atomic(path, write):
    """Replace `path` with what `write(stream)` produces; readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
