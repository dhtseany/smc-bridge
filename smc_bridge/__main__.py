import argparse
import os
import sys
from pathlib import Path

from .config import default_path


def main():
    parser = argparse.ArgumentParser(description="SMC-Mixer GUI (mapping editor) and background MIDI bridge")
    parser.add_argument("--config", type=Path, default=default_path(), help="mapping INI file location")
    parser.add_argument("--headless", action="store_true", help="run the background MIDI bridge without Qt or a display")
    plugins = parser.add_argument_group("plugins")
    plugins.add_argument("--list-plugins", action="store_true", help="show installed plugins and whether each is enabled")
    plugins.add_argument("--enable-plugin", metavar="NAME", help="turn a plugin on (a running bridge picks it up)")
    plugins.add_argument("--disable-plugin", metavar="NAME", help="turn a plugin off (a running bridge drops it)")
    plugins.add_argument("--no-plugins", action="store_true", help="with --headless: safe mode, load no plugins (also SMC_BRIDGE_NO_PLUGINS=1)")
    args = parser.parse_args()
    if args.list_plugins or args.enable_plugin or args.disable_plugin:
        return manage_plugins(parser, args)
    if args.headless:
        from .daemon import run
        return run(args.config.expanduser().resolve(), plugins=not (args.no_plugins or os.environ.get("SMC_BRIDGE_NO_PLUGINS")))
    try:
        from PySide6.QtWidgets import QApplication
        from .gui import Window
    except ImportError as error:
        parser.exit(1, f"Missing GUI dependency: {error}\nInstall PySide6; on Arch Linux: sudo pacman -S pyside6\n")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SMC Bridge")
    app.setOrganizationName("smc_bridge")
    window = Window(args.config.expanduser())
    window.show()
    return app.exec()


def manage_plugins(parser, args):
    from .plugins import installed, load_settings, set_enabled, settings_path
    path = settings_path(args.config.expanduser())
    available = installed()
    try:
        for name, enabled in ((args.enable_plugin, True), (args.disable_plugin, False)):
            if name is None:
                continue
            if enabled and name not in available:
                parser.exit(1, f"Plugin {name!r} is not installed. Installed: {', '.join(sorted(available)) or 'none'}\n")
            set_enabled(path, name, enabled)
            print(f"{name}: {'enabled' if enabled else 'disabled'} in {path}")
        if args.list_plugins:
            settings = load_settings(path)
            print(f"Plugin settings: {path}")
            for name in sorted(set(available) | set(settings)):
                entry = available.get(name)
                source = f"{entry.dist.name} {entry.dist.version}" if entry is not None and entry.dist else "not installed"
                state = "enabled" if name in settings and settings[name].enabled else "disabled"
                print(f"  {name:<16} {state:<9} {source}")
            if not available and not settings:
                print("  (no plugins installed)")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
