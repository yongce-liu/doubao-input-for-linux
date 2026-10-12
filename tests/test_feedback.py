import io
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from doubao_input.dbus_indicator import COLORS, LABELS, visual_state
from doubao_input.feedback import FeedbackClient


class IndicatorTests(unittest.TestCase):
    def test_connecting_is_real_recording_and_does_not_flicker_on_open(self):
        self.assertEqual(visual_state("connecting"), "recording")
        self.assertEqual(visual_state("recording"), "recording")
        self.assertEqual(visual_state("finishing"), "processing")
        self.assertEqual(visual_state("pasting"), "processing")

    def test_unknown_states_cannot_display_recognized_text(self):
        self.assertIsNone(visual_state("这是用户说的话"))

    def test_idle_and_stopped_mapping(self):
        self.assertEqual(visual_state("idle"), "idle")
        self.assertIsNone(visual_state("stopped"))

    def test_dock_idle_colors_are_available(self):
        for state in ("idle", "recording", "processing", "error", "login-required"):
            self.assertIn(state, COLORS)
            self.assertIn(state, LABELS)
        # 用户约定：绿=录音，黄=处理，红=错误，灰=待机
        self.assertGreater(COLORS["recording"][1], COLORS["recording"][0])
        self.assertGreater(COLORS["error"][0], COLORS["error"][1])
        self.assertGreater(COLORS["processing"][0], COLORS["processing"][2])
        self.assertGreater(COLORS["processing"][1], COLORS["processing"][2])


class FeedbackClientTests(unittest.TestCase):
    def test_source_indicator_uses_shared_cli_entry(self):
        import sys

        child = Mock()
        child.stdin.fileno.return_value = 10
        child.stdout.fileno.return_value = 11
        with (
            patch.dict(os.environ, DISPLAY=":1"),
            patch.object(sys, "frozen", False, create=True),
            patch("subprocess.Popen", return_value=child) as popen,
            patch("os.set_blocking"),
        ):
            FeedbackClient().start()
        self.assertEqual(
            popen.call_args.args[0],
            [
                sys.executable,
                "-m",
                "doubao_input",
                "_indicator",
                "--style",
                "dock",
            ],
        )

    def test_disabled_client_does_not_start_process(self):
        with patch("subprocess.Popen") as popen:
            FeedbackClient(enabled=False).start()
            popen.assert_not_called()

    def test_missing_display_is_safe(self):
        with patch.dict(os.environ, {}, clear=True), patch("subprocess.Popen") as popen:
            FeedbackClient().start()
            popen.assert_not_called()

    def test_child_process_receives_only_known_states(self):
        client = FeedbackClient()
        client.process = Mock()
        client.process.poll.return_value = None
        client.process.stdin = io.BytesIO()
        client.update("recording")
        client.update("private transcription")
        self.assertEqual(client.process.stdin.getvalue(), b"recording\n")

    def test_crashed_ui_does_not_raise_into_service(self):
        client = FeedbackClient()
        client.process = Mock()
        client.process.poll.return_value = 1
        client.update("recording")
        self.assertTrue(client.warned)

    def test_blocked_pipe_does_not_block_or_crash_audio_loop(self):
        client = FeedbackClient()
        client.process = Mock()
        client.process.poll.return_value = None
        client.process.stdin.write.side_effect = BlockingIOError
        client.update("recording")
        self.assertTrue(client.warned)

    def test_close_terminates_unresponsive_child(self):
        client = FeedbackClient()
        child = Mock()
        client.process = child
        child.wait.side_effect = [subprocess.TimeoutExpired("indicator", 1), None]
        client.close()
        child.stdin.close.assert_called_once()
        child.terminate.assert_called_once()
        self.assertIsNone(client.process)

    def test_badge_renders_offscreen(self):
        from doubao_input.dbus_indicator import icon_pixels

        for state in ("idle", "recording", "processing", "error", "login-required"):
            pixels = icon_pixels(state)
            self.assertEqual(len(pixels), 32 * 32 * 4)
            self.assertEqual(pixels[:4], bytes(4))
            self.assertTrue(any(pixels))


class IndicatorProcessTests(unittest.TestCase):
    def test_pipe_eof_hides_and_exits_without_polling(self):
        import sys

        code = """
from unittest.mock import Mock, AsyncMock, patch
from doubao_input.dbus_indicator import main
backend = Mock()
backend.call_notify = AsyncMock(return_value=1)
backend.call_close_notification = AsyncMock()
bus = Mock()
bus.introspect = AsyncMock(return_value=Mock())
bus.get_proxy_object.return_value.get_interface.return_value = backend
with patch('doubao_input.dbus_indicator.MessageBus') as factory, patch('doubao_input.dbus_indicator.Menu'):
    factory.return_value.connect = AsyncMock(return_value=bus)
    main(style='float')
assert [call.args[3] for call in backend.call_notify.call_args_list] == ['正在录音', '正在识别']
backend.call_close_notification.assert_awaited_once_with(1)
bus.disconnect.assert_called_once()
"""
        env = dict(os.environ, WAYLAND_DISPLAY="test-only", XDG_SESSION_TYPE="wayland")
        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            input="recording\nfinishing\nidle\n",
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class FeedbackEventTests(unittest.TestCase):
    def test_poll_events_parses_event_lines(self):
        client = FeedbackClient(enabled=False)
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        client.process = SimpleNamespace(stdout=io.FileIO(read_fd, "rb"))
        os.write(write_fd, b"event:toggle\nevent:mic:2\npartial")
        self.assertEqual(client.poll_events(), ["toggle", "mic:2"])
        # 未完成的半行留到下一次
        # "partial=hotkey..." 不构成事件前缀被丢弃，整行 quit 正常解析
        os.write(write_fd, b"=hotkey:right-shift\nevent:quit\n")
        self.assertEqual(client.poll_events(), ["quit"])
        os.close(write_fd)
        self.assertIsNone(client.poll_events())  # EOF
        os.close(read_fd)

    def test_poll_events_without_process(self):
        self.assertIsNone(FeedbackClient(enabled=False).poll_events())


class DisplayWaitTests(unittest.TestCase):
    def test_start_without_display_waits_instead_of_disabling(self):
        client = FeedbackClient(style="dock")
        with patch.dict(os.environ, {}, clear=True):
            client.start()
        self.assertTrue(client.awaiting_display)
        self.assertFalse(client.running)

    def test_no_indicator_never_waits(self):
        client = FeedbackClient(enabled=False, style="dock")
        with patch.dict(os.environ, {}, clear=True):
            client.start()
        self.assertFalse(client.awaiting_display)
        self.assertFalse(client.wanted)

    def test_style_none_is_not_wanted(self):
        self.assertFalse(FeedbackClient(style="none").wanted)
