import io
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from doubao_input.feedback import FeedbackClient
from doubao_input.indicator import IndicatorController, bottom_right, visual_state


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        # 浮标/通知语义：待机隐藏，错误自动消失
        self.backend.shows_idle = False
        self.backend.auto_hide_error = True
        self.scheduler = Mock()
        self.scheduler.timeout_add.return_value = 42
        self.controller = IndicatorController(self.backend, self.scheduler)

    def test_connecting_is_real_recording_and_does_not_flicker_on_open(self):
        self.controller.update("connecting")
        self.controller.update("recording")
        self.backend.show.assert_called_once_with("recording")

    def test_release_processing_and_idle_hides(self):
        self.controller.update("recording")
        self.controller.update("finishing")
        self.controller.update("pasting")
        self.controller.update("idle")
        self.assertEqual(self.backend.show.call_count, 2)
        self.backend.show.assert_called_with("processing")
        self.backend.hide.assert_called_once()

    def test_failure_expires_and_can_be_shown_again(self):
        self.controller.update("error")
        self.assertEqual(self.controller.timer, 42)
        self.controller.expire()
        self.backend.hide.assert_called_once()
        self.controller.update("error")
        self.assertEqual(self.backend.show.call_count, 2)

    def test_old_error_timer_cannot_hide_new_recording(self):
        self.controller.update("error")
        self.controller.update("recording")
        self.scheduler.source_remove.assert_called_once_with(42)
        self.assertIsNone(self.controller.timer)

    def test_close_removes_timer_and_hides(self):
        self.controller.update("login-required")
        self.controller.close()
        self.scheduler.source_remove.assert_called_once_with(42)
        self.backend.hide.assert_called_once()

    def test_position_respects_workarea_and_negative_monitor_origin(self):
        area = SimpleNamespace(x=-1920, y=0, width=1920, height=1040)
        self.assertEqual(bottom_right(area, 64, 64), (-88, 952))
        area = SimpleNamespace(x=0, y=0, width=50, height=50)
        self.assertEqual(bottom_right(area, 64, 64), (0, 0))

    def test_unknown_states_cannot_display_recognized_text(self):
        self.assertIsNone(visual_state("这是用户说的话"))

    def test_idle_and_stopped_mapping(self):
        self.assertEqual(visual_state("idle"), "idle")
        self.assertIsNone(visual_state("stopped"))

    def test_dock_backend_shows_idle_and_keeps_error(self):
        self.backend.shows_idle = True
        self.backend.auto_hide_error = False
        self.controller.update("idle")
        self.backend.show.assert_called_once_with("idle")
        self.controller.update("error")
        self.assertIsNone(self.controller.timer)  # Dock 上错误红图标保持可见
        self.controller.update("idle")
        self.assertEqual(self.backend.show.call_count, 3)

    def test_dock_idle_colors_are_available(self):
        from doubao_input.indicator_art import STATE_COLORS, STATE_LABELS

        for state in ("idle", "recording", "processing", "error", "login-required"):
            self.assertIn(state, STATE_COLORS)
            self.assertIn(state, STATE_LABELS)
        # 用户约定：绿=录音，黄=处理，红=错误，灰=待机
        self.assertGreater(STATE_COLORS["recording"][1], 0.7)  # 绿通道
        self.assertLess(STATE_COLORS["recording"][0], 0.3)
        self.assertGreater(STATE_COLORS["error"][0], 0.9)  # 红通道
        self.assertGreater(STATE_COLORS["processing"][0], 0.9)  # 黄=红+绿
        self.assertGreater(STATE_COLORS["processing"][1], 0.6)


class FeedbackClientTests(unittest.TestCase):
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
        import cairo

        from doubao_input.indicator_art import draw_badge

        for state in ("idle", "recording", "processing", "error", "login-required"):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
            draw_badge(cairo.Context(surface), state)
            self.assertTrue(any(surface.get_data()))


class IndicatorProcessTests(unittest.TestCase):
    def test_pipe_eof_hides_and_exits_without_polling(self):
        import sys

        code = """
from unittest.mock import Mock, patch
from doubao_input.indicator import main
backend = Mock()
backend.shows_idle = False
backend.auto_hide_error = True
with patch('doubao_input.indicator.NotificationBadge', return_value=backend):
    main()
assert [call.args[0] for call in backend.show.call_args_list] == ['recording', 'processing']
assert backend.hide.call_count >= 1
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
