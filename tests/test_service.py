import asyncio
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from doubao_input import service
from doubao_input.streaming import AuthenticationError


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(service, "AudioCapture"))
        self.stack.enter_context(patch.object(service, "Injector"))
        self.status = self.stack.enter_context(patch.object(service, "write_status"))
        self.params = self.stack.enter_context(patch.object(service, "ParamsStore"))
        self.stack.enter_context(
            patch.object(service, "selected_device", return_value=None)
        )
        self.engine = service.VoiceService()
        self.engine.loop = asyncio.get_running_loop()
        self.engine.injector.inject.return_value = True

    async def asyncTearDown(self):
        if self.engine.task:
            self.engine.task.cancel()
            await asyncio.gather(self.engine.task, return_exceptions=True)
            await asyncio.sleep(0)

    def mock_recognition(self, text="测试文本"):
        chunks = []

        async def recognize(params, queue, opened):
            opened()
            while (chunk := await queue.get()) is not None:
                chunks.append(chunk)
            return text

        self.stack.enter_context(
            patch.object(service, "recognize", side_effect=recognize)
        )
        return chunks

    async def finish(self):
        task = self.engine.task
        if task:
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)

    async def test_real_service_states_are_forwarded_without_transcription(self):
        self.mock_recognition(text="private words")
        with patch.object(self.engine.feedback, "update") as update:
            self.engine.press()
            self.engine.pressed_at -= 1
            self.engine.release()
            await self.finish()
        states = [call.args[0] for call in update.call_args_list]
        self.assertIn("connecting", states)
        self.assertIn("finishing", states)
        self.assertIn("pasting", states)
        self.assertEqual(states[-1], "idle")
        self.assertNotIn("private words", str(update.call_args_list))

    async def test_short_press_before_task_runs_does_not_stick(self):
        self.mock_recognition()
        self.engine.press()
        self.engine.release()
        await self.finish()
        self.assertIsNone(self.engine.task)
        self.assertEqual(self.engine.state, "idle")
        self.engine.injector.inject.assert_not_called()
        self.engine.press()
        self.assertIsNotNone(self.engine.task)

    async def test_two_sessions_are_isolated_and_paste_once_each(self):
        chunks = self.mock_recognition()
        for _ in range(2):
            self.engine.press()
            self.engine.enqueue(self.engine.token, b"audio")
            self.engine.pressed_at -= 1
            self.engine.release()
            await self.finish()
            self.assertEqual(self.engine.state, "idle")
            self.assertIsNone(self.engine.task)
        self.assertEqual(chunks, [b"audio", b"audio"])
        self.assertEqual(self.engine.injector.inject.call_count, 2)
        self.engine.injector.inject.assert_called_with("测试文本", use_shift=False)

    async def test_audio_scheduled_before_release_precedes_end_marker(self):
        chunks = self.mock_recognition()
        self.engine.press()
        callback = self.engine.capture.start.call_args.args[0]
        callback(b"last frame")
        self.engine.pressed_at -= 1
        self.engine.release()
        await self.finish()
        self.assertEqual(chunks, [b"last frame"])

    async def test_stale_audio_cannot_enter_next_session(self):
        self.mock_recognition()
        self.engine.press()
        old = self.engine.token
        self.engine.release()
        await self.finish()
        self.engine.press()
        self.engine.enqueue(old, b"old")
        self.assertEqual(self.engine.queue.qsize(), 0)

    async def test_missing_credentials_does_not_record_or_open_gui(self):
        self.params.load.return_value = None
        self.engine.press()
        self.assertEqual(self.engine.state, "login-required")
        self.engine.capture.start.assert_not_called()
        self.assertIsNone(self.engine.task)

    async def test_buffer_overflow_cancels_without_paste(self):
        self.mock_recognition()
        self.engine.press()
        token = self.engine.token
        for _ in range(service.MAX_AUDIO_BLOCKS + 1):
            self.engine.enqueue(token, b"audio")
        await self.finish()
        self.assertEqual(self.engine.state, "error")
        self.engine.injector.inject.assert_not_called()

    async def test_network_error_preserves_credentials(self):
        async def fail(*args):
            raise OSError("offline")

        self.stack.enter_context(patch.object(service, "recognize", side_effect=fail))
        self.engine.press()
        await self.finish()
        self.assertEqual(self.engine.state, "error")
        self.params.clear.assert_not_called()
        self.engine.injector.inject.assert_not_called()

    async def test_auth_failure_requires_login_without_gui(self):
        async def fail(*args):
            raise AuthenticationError("expired")

        self.stack.enter_context(patch.object(service, "recognize", side_effect=fail))
        self.engine.press()
        await self.finish()
        self.assertEqual(self.engine.state, "login-required")
        self.params.clear.assert_called_once()

    async def test_repeated_press_does_not_restart_recording(self):
        self.mock_recognition()
        self.engine.press()
        task = self.engine.task
        self.engine.press()
        self.assertIs(self.engine.task, task)
        self.engine.capture.start.assert_called_once()

    async def test_empty_result_does_not_paste(self):
        self.mock_recognition(text="")
        self.engine.press()
        self.engine.pressed_at -= 1
        self.engine.release()
        await self.finish()
        self.engine.injector.inject.assert_not_called()

    async def test_recording_start_error_can_retry(self):
        self.engine.capture.start.side_effect = RuntimeError("device missing")
        self.engine.press()
        self.assertIsNone(self.engine.task)
        self.assertEqual(self.engine.state, "error")

    async def test_server_finishes_early_waits_for_key_release(self):
        async def finished(*args):
            return "test"

        self.stack.enter_context(
            patch.object(service, "recognize", side_effect=finished)
        )
        self.engine.press()
        await asyncio.sleep(0.03)
        self.engine.injector.inject.assert_not_called()
        self.engine.pressed_at -= 1
        self.engine.release()
        await self.finish()
        self.engine.injector.inject.assert_called_once()

    async def test_tray_click_toggles_recording_like_ctrl(self):
        self.mock_recognition()
        self.engine.stop_event = asyncio.Event()
        self.engine.handle_event("toggle")
        self.assertIsNotNone(self.engine.task)
        self.assertEqual(self.engine.state, "connecting")
        self.engine.pressed_at -= 1
        self.engine.handle_event("toggle")
        await self.finish()
        self.assertEqual(self.engine.state, "idle")
        self.engine.injector.inject.assert_called_once()

    async def test_tray_quit_sets_stop_event(self):
        self.engine.stop_event = asyncio.Event()
        self.engine.handle_event("quit")
        self.assertTrue(self.engine.stop_event.is_set())

    async def test_tray_hotkey_restart_uses_saved_key(self):
        from unittest.mock import Mock

        self.engine.stop_event = asyncio.Event()
        old_trigger = Mock()
        self.engine.trigger = old_trigger
        with (
            patch.object(service, "save_hotkey") as save,
            patch.object(service, "load_hotkey", return_value="right-shift"),
            patch.object(service, "EvdevPtt") as ptt,
        ):
            self.engine.set_hotkey("right-shift")
            save.assert_called_once_with("right-shift")
            self.assertEqual(ptt.call_args.kwargs["key_code"], 54)
            old_trigger.stop.assert_called_once()

    async def test_tray_unknown_hotkey_reports_error(self):
        self.engine.stop_event = asyncio.Event()
        self.engine.handle_event("hotkey:bogus")
        self.assertEqual(self.engine.state, "error")

    async def test_service_picks_up_tray_icon_when_display_appears_late(self):
        """开机自启竞态：先无 DISPLAY，桌面就绪后补挂状态图标。"""
        self.engine.stop_event = asyncio.Event()
        self.engine.feedback = Mock(
            wanted=True, running=False, has_display=lambda: True
        )
        self.engine.feedback.event_fd.return_value = None
        task = asyncio.create_task(self.engine._await_display(interval=0.01))
        await asyncio.sleep(0.05)
        self.engine._closing = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.engine.feedback.start.assert_called()

    async def test_no_display_keeps_waiting_without_starting_icon(self):
        self.engine.stop_event = asyncio.Event()
        self.engine.feedback = Mock(
            wanted=True, running=False, has_display=lambda: False
        )
        task = asyncio.create_task(self.engine._await_display(interval=0.01))
        await asyncio.sleep(0.05)
        self.engine._closing = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.engine.feedback.start.assert_not_called()
