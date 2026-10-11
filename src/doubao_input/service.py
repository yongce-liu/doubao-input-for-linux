"""无 GTK/WebKit 的用户会话后台服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import time

from doubao_input.doubao.audio_capture import AudioCapture
from doubao_input.doubao.config import PASTE_DELAY, get_audio_config_path
from doubao_input.doubao.params_store import ParamsStore
from doubao_input.feedback import FeedbackClient
from doubao_input.inject.injector import Injector
from doubao_input.runtime import InstanceLock, write_status
from doubao_input import session_env
from doubao_input.streaming import AuthenticationError, recognize
from doubao_input.trigger.evdev_ptt import (
    HOTKEYS,
    EvdevPtt,
    load_hotkey,
    save_hotkey,
)

logger = logging.getLogger(__name__)
MIN_PRESS_DURATION = 0.15
MAX_AUDIO_BLOCKS = 64  # 有界缓冲；断网时不无限增长。
MAX_RECORDING_SECONDS = 120


def selected_device():
    try:
        return json.loads(get_audio_config_path().read_text()).get("device")
    except FileNotFoundError:
        return None


class VoiceService:
    def __init__(self, *, use_shift=False, indicator=True, indicator_style="dock"):
        self.feedback = FeedbackClient(enabled=indicator, style=indicator_style)
        self.capture = AudioCapture()
        self.injector = Injector()
        self.use_shift = use_shift
        self.task = None
        self.queue = None
        self.token = None
        self.pressed_at = None
        self.limit_timer = None
        self.loop = None
        self.stop_event = None
        self.state = "idle"
        self.trigger = None
        self.login_task = None
        self.event_fd = None
        self.display_task = None
        self._closing = False

    def set_state(self, state, error=None):
        self.state = state
        write_status(state, error)
        self.feedback.update(state)
        logger.info("state=%s%s", state, f": {error}" if error else "")

    def press(self):
        if self.task is not None or self._closing:
            return
        params = ParamsStore.load()
        if params is None:
            self.set_state(
                "login-required", "请运行 doubao-input login；后台不会自动弹窗"
            )
            return
        token = object()
        self.token = token
        self.queue = asyncio.Queue(maxsize=MAX_AUDIO_BLOCKS)
        self.pressed_at = time.monotonic()
        try:
            self.capture.set_device(selected_device())
            self.capture.start(
                lambda data: self.loop.call_soon_threadsafe(self.enqueue, token, data)
            )
        except Exception as exc:  # noqa: BLE001 -- 设备/网络边界必须恢复到可重试状态
            self.capture.stop()
            self.token = None
            self.pressed_at = None
            self.set_state("error", f"麦克风启动失败: {exc}")
            return
        self.set_state("connecting")
        self.task = asyncio.create_task(self.transcribe(params, token, self.queue))
        self.task.add_done_callback(self.session_done)
        self.limit_timer = self.loop.call_later(
            MAX_RECORDING_SECONDS,
            self.abort,
            "录音超过 120 秒，已取消；请松开 Ctrl 后重试",
        )

    def enqueue(self, token, data):
        if token is not self.token or self.queue is None:
            return
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            self.abort("音频缓冲已满，已取消本次录音；请检查网络")

    def abort(self, message):
        self.capture.stop()
        self.token = None
        self.set_state("error", message)
        if self.task:
            self.task.cancel()

    def handle_event(self, event):
        """托盘图标交互：左键模拟按下/松开，右键菜单命令。"""
        if self._closing:
            return
        if event == "toggle":
            if self.task is None:
                self.press()
            elif self.pressed_at is not None:
                self.release()
        elif event == "login":
            self.start_login()
        elif event == "quit":
            logger.info("托盘菜单请求退出")
            self.stop_event.set()
        elif event.startswith("mic:"):
            self.set_mic(event[4:])
        elif event.startswith("hotkey:"):
            self.set_hotkey(event[7:])
        elif event:
            logger.debug("忽略未知浮标事件: %r", event)

    def set_mic(self, value):
        try:
            device = None if value == "auto" else int(value)
            if device is not None and device not in {
                d["index"] for d in AudioCapture.list_input_devices()
            }:
                raise ValueError("无效的输入设备编号")
            get_audio_config_path().write_text(json.dumps({"device": device}))
            logger.info("麦克风已切换: %s（下次录音生效）", value)
        except (OSError, ValueError) as exc:
            self.set_state("error", f"麦克风设置失败: {exc}")

    def set_hotkey(self, name):
        if name not in HOTKEYS:
            self.set_state("error", f"未知快捷键: {name}")
            return
        try:
            save_hotkey(name)
        except OSError as exc:
            self.set_state("error", f"快捷键保存失败: {exc}")
            return
        logger.info("快捷键已切换: %s", HOTKEYS[name][0])
        # 按住旧键时切换会丢失松开事件；先按松开处理再换监听。
        if self.pressed_at is not None:
            self.release()
        if self.trigger is not None:
            self.trigger.stop()
            self._start_trigger()

    def _start_trigger(self):
        _label, key_code = HOTKEYS[load_hotkey()]
        self.trigger = EvdevPtt(
            self.press,
            self.release,
            on_error=lambda message: self.set_state("error", message),
            dispatch=self.loop.call_soon_threadsafe,
            key_code=key_code,
        )
        self.trigger.start()

    def start_login(self):
        if self.login_task is not None and not self.login_task.done():
            logger.info("登录窗口已在打开中")
            return
        command = (
            [sys.executable, "login"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "doubao_input", "login"]
        )
        self.login_task = asyncio.create_task(self._login_and_refresh(command))

    async def _login_and_refresh(self, command):
        try:
            process = await asyncio.create_subprocess_exec(*command)
            await process.wait()
        except (OSError, asyncio.CancelledError) as exc:
            logger.warning("登录进程失败: %s", exc)
            return
        # 登录窗口关闭后刷新凭证状态（下一次按键也会自动重读）。
        if not self._closing and self.state in ("idle", "login-required"):
            self.set_state("idle" if ParamsStore.load() else "login-required")

    def _register_feedback_reader(self):
        fd = self.feedback.event_fd()
        if fd is None or fd == self.event_fd:
            return
        self._unregister_feedback_reader()
        self.event_fd = fd
        self.loop.add_reader(fd, self._read_feedback_events)

    def _unregister_feedback_reader(self):
        if self.event_fd is None:
            return
        self.loop.remove_reader(self.event_fd)
        self.event_fd = None

    def _read_feedback_events(self):
        events = self.feedback.poll_events()
        if events is None:
            # 图标进程退出；停止读取，服务继续运行。
            self._unregister_feedback_reader()
            return
        for event in events:
            self.handle_event(event)

    async def _await_display(self, interval=5.0, max_interval=30.0):
        """开机自启时桌面会话可能晚于本服务就绪，就绪后补挂状态图标。

        systemd 用户服务启动时环境里通常没有 DISPLAY（桌面会话稍后才导入，
        而进程环境是快照），因此每次都用 session_env 主动解析，而不是读
        自己那份过期的 os.environ。
        """
        delay = interval
        while not self._closing:
            await asyncio.sleep(delay)
            if self._closing:
                return
            if not self.feedback.wanted:
                continue
            if self.feedback.running:
                delay = interval
                continue
            session_env.ensure()
            if not self.feedback.has_display():
                delay = min(delay * 2, max_interval)
                continue
            delay = interval
            self.feedback.start()
            self._register_feedback_reader()

    def release(self):
        if self.task is None or self.pressed_at is None:
            return
        duration = time.monotonic() - self.pressed_at
        self.pressed_at = None
        self.capture.stop()
        if self.limit_timer:
            self.limit_timer.cancel()
            self.limit_timer = None
        if duration < MIN_PRESS_DURATION:
            self.token = None
            self.task.cancel()
            return
        self.set_state("finishing")
        # stop() 已停止音频线程；已排入主循环的音频先于 sentinel 处理。
        self.loop.call_soon(self.enqueue, self.token, None)

    async def transcribe(self, params, token, queue):
        try:

            def opened():
                if self.pressed_at is not None:
                    self.set_state("recording")

            text = await recognize(params, queue, opened)
            self.capture.stop()
            # 若服务端先结束，等用户真正松开 Ctrl 再粘贴。
            while self.pressed_at is not None:
                await asyncio.sleep(0.02)
            if text and token is self.token and not self._closing:
                self.set_state("pasting")
                await asyncio.sleep(PASTE_DELAY)
                paste = asyncio.create_task(
                    asyncio.to_thread(
                        self.injector.inject, text, use_shift=self.use_shift
                    )
                )
                try:
                    ok = await asyncio.shield(paste)
                except asyncio.CancelledError:
                    # 无法取消系统注入线程；必须等它释放按键，才清理 injector。
                    await paste
                    raise
                if not ok:
                    raise RuntimeError("粘贴失败，请检查剪贴板工具和 /dev/uinput 权限")
            self.set_state("idle")
        except AuthenticationError as exc:
            ParamsStore.clear()
            self.set_state("login-required", str(exc))
        except asyncio.CancelledError:
            if self.state != "error":
                self.set_state("idle")
            raise
        except Exception as exc:  # noqa: BLE001 -- 设备/网络边界必须恢复到可重试状态
            # 不把网络异常的完整 URL 写入日志，避免泄漏身份参数。
            self.set_state(
                "error", f"识别失败 ({type(exc).__name__})；请检查网络、登录和设备权限"
            )
        finally:
            self.capture.stop()

    def session_done(self, task):
        # 即使在协程首次执行前取消，仍要清理状态，允许下一次按键。
        self.capture.stop()
        if self.limit_timer:
            self.limit_timer.cancel()
            self.limit_timer = None
        self.token = None
        self.queue = None
        self.pressed_at = None
        self.task = None
        if task.cancelled() and self.state not in ("error", "stopped"):
            self.set_state("idle")

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, self.stop_event.set)
        session_env.ensure()
        self.feedback.start()
        self._register_feedback_reader()
        if self.feedback.wanted:
            self.display_task = asyncio.create_task(self._await_display())
        self.set_state("idle" if ParamsStore.load() else "login-required")
        try:
            self._start_trigger()
            await self.stop_event.wait()
        finally:
            self._closing = True
            self._unregister_feedback_reader()
            if self.display_task is not None:
                self.display_task.cancel()
                await asyncio.gather(self.display_task, return_exceptions=True)
            if self.trigger is not None:
                self.trigger.stop()
            if self.login_task is not None and not self.login_task.done():
                self.login_task.cancel()
            self.capture.stop()
            if self.task:
                # 粘贴线程不能强制中断；等其释放虚拟修饰键再退出。
                if self.state != "pasting":
                    self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.injector.close()
            self.set_state("stopped")
            await asyncio.to_thread(self.feedback.close)
            for sig in (signal.SIGINT, signal.SIGTERM):
                self.loop.remove_signal_handler(sig)


def run_service(*, use_shift=False, indicator=True, indicator_style="dock"):
    with InstanceLock():
        asyncio.run(
            VoiceService(
                use_shift=use_shift,
                indicator=indicator,
                indicator_style=indicator_style,
            ).run()
        )
    return 0
