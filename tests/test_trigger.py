import struct
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from doubao_input.trigger.evdev_ptt import EV_KEY, KEY_RIGHTCTRL, EvdevPtt


class TriggerTests(unittest.TestCase):
    def test_dispatch_preserves_press_release_and_ignores_repeat(self):
        press, release = Mock(), Mock()
        trigger = EvdevPtt(press, release, dispatch=lambda fn, *a: fn(*a))
        payload = b"".join(
            struct.pack("llHHi", 0, 0, EV_KEY, KEY_RIGHTCTRL, v) for v in (1, 2, 0)
        )
        trigger._dispatch(payload, trigger._dispatch_callback)
        press.assert_called_once()
        release.assert_called_once()

    def test_missing_device_still_starts_retry_worker(self):
        trigger = EvdevPtt(Mock(), Mock())
        with (
            patch.object(trigger, "_scan", return_value=False),
            patch("doubao_input.trigger.evdev_ptt.threading.Thread") as thread,
        ):
            self.assertFalse(trigger.start())
            thread.return_value.start.assert_called_once()

    def test_scan_preserves_existing_fd_and_closes_probe_device(self):
        trigger = EvdevPtt(Mock(), Mock())
        trigger._paths, trigger._fds = ["/dev/input/event1"], [100]
        probe = Mock()
        probe.capabilities.return_value = {EV_KEY: [KEY_RIGHTCTRL]}
        with (
            patch(
                "evdev.list_devices",
                return_value=["/dev/input/event1", "/dev/input/event2"],
            ),
            patch("evdev.InputDevice", return_value=probe),
            patch("os.open", return_value=101),
            patch("os.close") as close,
        ):
            self.assertTrue(trigger._scan())
            self.assertEqual(trigger._fds, [100, 101])
            probe.close.assert_called_once()
            close.assert_not_called()

    def test_error_dispatch_does_not_require_glib(self):
        error = Mock()
        trigger = EvdevPtt(
            Mock(), Mock(), on_error=error, dispatch=lambda fn, *a: fn(*a)
        )
        trigger._notify_error("missing keyboard")
        error.assert_called_once_with("missing keyboard")


class WindowShortcutTests(unittest.TestCase):
    """终端识别：Ctrl+V 在终端里是 readline 的 quoted-insert，必须换键。"""

    def test_known_terminals_use_ctrl_shift_v(self):
        from doubao_input.window_info import terminal_shortcut

        for name in (
            "gnome-terminal-server",
            "org.gnome.terminal",
            "xfce4-terminal",
            "konsole",
            "alacritty",
            "kitty",
            "tabby",
            "tilix",
            "wezterm",
            "foot",
        ):
            self.assertEqual(
                terminal_shortcut(name), "ctrl-shift-v", f"{name} 应使用 Ctrl+Shift+V"
            )

    def test_xterm_family_uses_shift_insert(self):
        from doubao_input.window_info import terminal_shortcut

        for name in ("xterm", "urxvt", "rxvt"):
            self.assertEqual(
                terminal_shortcut(name), "shift-insert", f"{name} 应使用 Shift+Insert"
            )

    def test_regular_apps_are_not_terminals(self):
        from doubao_input.window_info import terminal_shortcut

        for name in ("chatgpt", "firefox", "code", "gedit", "cinnamon", ""):
            self.assertIsNone(terminal_shortcut(name), f"{name} 不应识别为终端")

    def test_active_window_class_parses_xprop_output(self):
        from doubao_input import window_info

        outputs = {
            "_NET_ACTIVE_WINDOW": "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x3e00007\n",
            "WM_CLASS": 'WM_CLASS(STRING) = "tabby", "tabby"\n',
        }

        def fake_run(argv, **kwargs):
            key = "_NET_ACTIVE_WINDOW" if "_NET_ACTIVE_WINDOW" in argv else "WM_CLASS"
            return SimpleNamespace(returncode=0, stdout=outputs[key])

        with patch.object(window_info, "_x11_available", return_value=True), patch.object(
            window_info.subprocess, "run", side_effect=fake_run
        ), patch.object(
            window_info, "command_candidates", return_value=[["xprop"]]
        ):
            self.assertEqual(window_info.active_window_class(), "tabby")

    def test_active_window_class_ignores_zero_window(self):
        from doubao_input import window_info

        with patch.object(window_info, "_x11_available", return_value=True), patch.object(
            window_info, "command_candidates", return_value=[["xprop"]]
        ), patch.object(
            window_info.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=0, stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0x0\n"
            ),
        ):
            self.assertIsNone(window_info.active_window_class())

    def test_no_x11_returns_none(self):
        from doubao_input import window_info

        with patch.object(window_info, "_x11_available", return_value=False):
            self.assertIsNone(window_info.active_window_class())
