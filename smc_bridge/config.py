"""Persistent mappings, independent of the GUI and the MIDI transport."""
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile


@dataclass(frozen=True)
class Mapping:
    name: str = ""
    volume_cc: int | None = None
    pan_cc: int | None = None
    mute_cc: int | None = None
    solo_cc: int | None = None

    @property
    def assigned(self):
        return self.volume_cc is not None and self.pan_cc is not None


def default_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "smc_bridge" / "mappings.ini"


def validate(mappings):
    if len(mappings) != 8:
        raise ValueError("Exactly eight strip mappings are required.")
    used = {}
    for strip, mapping in enumerate(mappings, 1):
        if "\n" in mapping.name or "\r" in mapping.name:
            raise ValueError(f"Strip {strip}: channel names must fit on one line.")
        if (mapping.volume_cc is None) != (mapping.pan_cc is None):
            raise ValueError(f"Strip {strip}: assign both volume and pan CCs, or neither.")
        if mapping.assigned and not mapping.name.strip():
            raise ValueError(f"Strip {strip}: enter a channel name.")
        if not mapping.assigned and (mapping.mute_cc is not None or mapping.solo_cc is not None):
            raise ValueError(f"Strip {strip}: assign the strip before binding mute/solo.")
        for control, cc in (
            ("volume", mapping.volume_cc), ("pan", mapping.pan_cc),
            ("mute", mapping.mute_cc), ("solo", mapping.solo_cc),
        ):
            if cc is None:
                continue
            if type(cc) is not int or not 0 <= cc <= 127:
                raise ValueError(f"Strip {strip}: {control} CC must be between 0 and 127.")
            if cc in used:
                raise ValueError(f"CC {cc} is already used by {used[cc]}. Choose a unique CC for each control.")
            used[cc] = f"strip {strip} {control}"


def load(path):
    if not path.exists():
        return [Mapping() for _ in range(8)]
    parser = ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
        if parser.getint("app", "format_version") != 1:
            raise ValueError("Unsupported configuration version.")
        if set(parser.sections()) != {"app", *(f"strip{i}" for i in range(1, 9))}:
            raise ValueError("Configuration must contain app and strip1 through strip8 sections.")
        result = []
        for i in range(1, 9):
            section = parser[f"strip{i}"]
            def cc(key):
                value = section.get(key, "").strip()
                return int(value) if value else None
            result.append(Mapping(section.get("name", ""), cc("volume_cc"), cc("pan_cc"), cc("mute_cc"), cc("solo_cc")))
        validate(result)
        return result
    except (ConfigError, KeyError) as error:
        raise ValueError(f"Invalid configuration: {error}") from error


def save(path, mappings):
    validate(mappings)
    parser = ConfigParser(interpolation=None)
    parser["app"] = {"format_version": "1"}
    for i, mapping in enumerate(mappings, 1):
        parser[f"strip{i}"] = {
            "name": mapping.name,
            "volume_cc": "" if mapping.volume_cc is None else str(mapping.volume_cc),
            "pan_cc": "" if mapping.pan_cc is None else str(mapping.pan_cc),
            "mute_cc": "" if mapping.mute_cc is None else str(mapping.mute_cc),
            "solo_cc": "" if mapping.solo_cc is None else str(mapping.solo_cc),
        }
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
