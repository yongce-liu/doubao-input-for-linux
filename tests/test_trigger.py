import struct
import unittest
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
