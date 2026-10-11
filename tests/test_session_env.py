"""会话环境解析测试（不依赖真实桌面）。"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from doubao_input import session_env

CLEAR = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE")


class SessionEnvTests(unittest.TestCase):
    def setUp(self):
        for key in CLEAR:
            os.environ.pop(key, None)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in CLEAR])

    def test_existing_display_short_circuits(self):
        os.environ["DISPLAY"] = ":7"
        with patch.object(session_env, "_detect") as detect:
            self.assertTrue(session_env.ensure())
        detect.assert_not_called()
        self.assertEqual(os.environ["DISPLAY"], ":7")

    def test_loginctl_fills_display_and_authority(self):
        def fake_run(argv):
            if "list-sessions" in argv:
                return "c1 4242 yongce seat0 tty7 active no -\n"
            return "Display=:1\nType=x11\nActive=yes\n"

        with patch.object(session_env, "_run", side_effect=fake_run), patch.object(
            session_env.os, "getuid", return_value=4242
        ), patch.object(Path, "exists", return_value=True):
            self.assertTrue(session_env.ensure())
        self.assertEqual(os.environ["DISPLAY"], ":1")
        self.assertEqual(os.environ["XDG_SESSION_TYPE"], "x11")
        self.assertTrue(os.environ["XAUTHORITY"].endswith(".Xauthority"))

    def test_inactive_session_is_skipped(self):
        def fake_run(argv):
            if "list-sessions" in argv:
                return "c1 4242 yongce seat0 tty7 active no -\n"
            return "Display=:1\nType=x11\nActive=no\n"

        with patch.object(session_env, "_run", side_effect=fake_run), patch.object(
            session_env.os, "getuid", return_value=4242
        ), patch.object(session_env, "_from_systemd_user_env", return_value={}):
            with patch.object(session_env, "_from_x11_socket", return_value={}):
                self.assertFalse(session_env.ensure())

    def test_systemd_user_env_used_when_loginctl_silent(self):
        with patch.object(session_env, "_from_loginctl", return_value={}), patch.object(
            session_env,
            "_from_systemd_user_env",
            return_value={"DISPLAY": ":0", "XAUTHORITY": "/tmp/xauth"},
        ):
            self.assertTrue(session_env.ensure())
        self.assertEqual(os.environ["DISPLAY"], ":0")

    def test_x11_socket_last_resort(self):
        with patch.object(session_env, "_from_loginctl", return_value={}), patch.object(
            session_env, "_from_systemd_user_env", return_value={}
        ):
            with patch.object(
                session_env, "_from_x11_socket", return_value={"DISPLAY": ":3"}
            ):
                self.assertTrue(session_env.ensure())
        self.assertEqual(os.environ["DISPLAY"], ":3")

    def test_wayland_session_sets_wayland_display(self):
        props = {"Type": "wayland", "Display": "", "Active": "yes"}
        with patch.object(session_env, "_wayland_socket", return_value="wayland-0"):
            found = session_env._env_from_session(props)
        self.assertEqual(found["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(found["XDG_SESSION_TYPE"], "wayland")
        self.assertNotIn("DISPLAY", found)

    def test_no_display_anywhere_returns_false(self):
        with patch.object(session_env, "_detect", return_value={}):
            self.assertFalse(session_env.ensure())
