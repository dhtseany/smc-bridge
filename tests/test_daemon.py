import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from smc_bridge.config import Mapping, save
from smc_bridge.daemon import ConfigurationState, InstanceLock


class DaemonTests(unittest.TestCase):
    def test_reload_keeps_last_valid_mappings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mappings.ini"
            state = ConfigurationState(path)
            state.refresh(initial=True)
            mappings = [Mapping("Mic", 11, 12)] + [Mapping() for _ in range(7)]
            save(path, mappings)
            self.assertTrue(state.refresh())
            self.assertEqual(state.mappings, mappings)
            path.write_text("bad config")
            with self.assertLogs(level="ERROR"):
                self.assertFalse(state.refresh())
            self.assertEqual(state.mappings, mappings)
            path.unlink()
            with self.assertLogs(level="ERROR"):
                self.assertFalse(state.refresh())
            self.assertEqual(state.mappings, mappings)
            save(path, [Mapping() for _ in range(8)])
            self.assertTrue(state.refresh())
            self.assertFalse(state.mappings[0].assigned)

    def test_instance_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_RUNTIME_DIR": directory}):
            with InstanceLock():
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with InstanceLock():
                        pass
            with InstanceLock():
                pass

    def test_headless_without_site_packages_and_clean_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, XDG_RUNTIME_DIR=directory)
            env.pop("DISPLAY", None)
            env.pop("WAYLAND_DISPLAY", None)
            path = Path(directory) / "mappings.ini"
            process = subprocess.Popen(
                [sys.executable, "-S", "-m", "smc_bridge", "--headless", "--config", str(path)],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                # Bounded readiness wait without relying on a fixed sleep.
                import selectors
                selector = selectors.DefaultSelector()
                selector.register(process.stderr, selectors.EVENT_READ)
                import time
                deadline = time.monotonic() + 5
                output = ""
                while time.monotonic() < deadline and "Headless setup preview running" not in output:
                    if selector.select(.1):
                        output += os.read(process.stderr.fileno(), 4096).decode()
                selector.close()
                self.assertIn("Headless setup preview running", output)
                process.send_signal(signal.SIGTERM)
                _, remaining = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, remaining)
                self.assertIn("stopped cleanly", remaining)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
