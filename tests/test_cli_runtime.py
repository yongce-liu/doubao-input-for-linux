import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from doubao_input import __main__ as cli
from doubao_input.runtime import InstanceLock, read_status, write_status


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(
            os.environ,
            {"XDG_RUNTIME_DIR": directory.name, "XDG_CONFIG_HOME": directory.name},
        )
        env.start()
        self.addCleanup(env.stop)
        self.root = Path(directory.name)

    def test_single_instance_and_status(self):
        self.assertEqual(read_status()["state"], "stopped")
        with InstanceLock():
            write_status("recording")
            self.assertEqual(read_status()["state"], "recording")
            with self.assertRaises(RuntimeError), InstanceLock():
                pass
        self.assertEqual(read_status()["state"], "stopped")

    def test_service_import_does_not_import_gui(self):
        code = """
import sys
from doubao_input import service
assert 'gi' not in sys.modules
assert not any('doubao_input.ui' in name for name in sys.modules)
assert 'doubao_input.login_app' not in sys.modules
"""
        subprocess.run([sys.executable, "-c", code], check=True)

    def test_default_command_runs_service(self):
        with patch("doubao_input.service.run_service", return_value=0) as run:
            self.assertEqual(cli.main([]), 0)
            run.assert_called_once_with(
                paste_shortcut="auto", indicator=True, indicator_style="dock"
            )

    def test_indicator_can_be_disabled(self):
        with patch("doubao_input.service.run_service", return_value=0) as run:
            cli.main(["serve", "--no-indicator"])
            run.assert_called_once_with(
                paste_shortcut="auto", indicator=False, indicator_style="dock"
            )

    def test_terminal_paste_is_explicit(self):
        with patch("doubao_input.service.run_service", return_value=0) as run:
            cli.main(["serve", "--paste-shortcut", "ctrl-shift-v"])
            run.assert_called_once_with(
                paste_shortcut="ctrl-shift-v", indicator=True, indicator_style="dock"
            )

    def test_install_service_does_not_start_or_overwrite(self):
        cli.install_service()
        path = self.root / "systemd/user/doubao-input.service"
        content = path.read_text()
        self.assertIn("-m doubao_input serve", content)
        self.assertNotIn("DISPLAY=:0", content)
        with self.assertRaises(RuntimeError):
            cli.install_service()
        self.assertEqual(path.read_text(), content)

    def test_login_credentials_saved_privately(self):
        from doubao_input.doubao.config import get_params_path
        from doubao_input.doubao.params_store import ASRParams, ParamsStore

        params = ASRParams({"session": "test"}, "device", "web")
        ParamsStore.save(params)
        self.assertEqual(ParamsStore.load(), params)
        self.assertEqual(get_params_path().stat().st_mode & 0o777, 0o600)

    def test_service_process_sigterm_releases_instance_lock(self):
        import signal
        import time

        code = """
from unittest.mock import patch
from doubao_input.service import run_service
with patch('doubao_input.service.EvdevPtt'):
    run_service(indicator=False)
"""
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if read_status()["state"] == "login-required":
                    break
                if process.poll() is not None:
                    self.fail(process.communicate()[1])
                time.sleep(0.02)
            else:
                self.fail("service did not start")
            process.send_signal(signal.SIGTERM)
            _stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(read_status()["state"], "stopped")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()


class RuntimeDirTests(unittest.TestCase):
    def test_falls_back_to_logind_dir_without_env(self):
        from doubao_input import runtime

        with patch.dict(os.environ, {}, clear=True):
            if not Path(f"/run/user/{os.getuid()}").is_dir():
                self.skipTest("no /run/user fallback on this host")
            self.assertEqual(
                runtime.runtime_dir(), Path(f"/run/user/{os.getuid()}") / "doubao-input"
            )

    def test_honours_xdg_runtime_dir(self):
        from doubao_input import runtime

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
                self.assertEqual(runtime.runtime_dir(), Path(tmp) / "doubao-input")
