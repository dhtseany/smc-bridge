import argparse
import sys
from pathlib import Path

from .config import default_path


def main():
    parser = argparse.ArgumentParser(description="SMC-Mixer GUI (mapping editor) and background MIDI bridge")
    parser.add_argument("--config", type=Path, default=default_path(), help="mapping INI file location")
    parser.add_argument("--headless", action="store_true", help="run the background MIDI bridge without Qt or a display")
    args = parser.parse_args()
    if args.headless:
        from .daemon import run
        return run(args.config.expanduser().resolve())
    try:
        from PySide6.QtWidgets import QApplication
        from .gui import Window
    except ImportError as error:
        parser.exit(1, f"Missing GUI dependency: {error}\nInstall PySide6; on Arch Linux: sudo pacman -S pyside6\n")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("SMC Mixer")
    app.setOrganizationName("smc_mixer_mac2cc")
    window = Window(args.config.expanduser())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
