"""状态浮标子进程控制；语音核心不导入任何 GUI 库。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)
STATES = frozenset(
    {
        "idle",
        "connecting",
        "recording",
        "finishing",
        "pasting",
        "error",
        "login-required",
        "stopped",
    }
)


class FeedbackClient:
    def __init__(self, enabled=True, style="dock"):
        self.enabled = enabled
        self.style = style
        self.process = None
        self.warned = False
        self.awaiting_display = False
        self.event_buffer = b""

    @property
    def wanted(self):
        """是否期望有状态图标（用于决定要不要等待桌面会话）。"""
        return self.enabled and self.style != "none"

    @staticmethod
    def has_display():
        return bool(
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        )

    def start(self):
        if not self.wanted:
            return
        if not self.has_display():
            # 开机自启时 systemd 可能早于桌面会话导入 DISPLAY；稍后重试。
            self.awaiting_display = True
            if not self.warned:
                logger.warning("尚未检测到桌面会话，状态图标稍后自动重试")
                self.warned = True
            return
        self.awaiting_display = False
        try:
            command = (
                [sys.executable, "_indicator"]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "doubao_input.indicator"]
            ) + ["--style", self.style]
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,  # 浮标点击/菜单事件回传
                start_new_session=True,
                bufsize=0,
            )
            os.set_blocking(self.process.stdin.fileno(), False)
            os.set_blocking(self.process.stdout.fileno(), False)
        except OSError as exc:
            logger.warning("无法启动状态浮标: %s", exc)
            self.close()

    def update(self, state):
        if state not in STATES or self.process is None:
            return
        try:
            if self.process.poll() is not None:
                raise BrokenPipeError("状态浮标进程已退出")
            # 仅发送固定状态名称，不发送语音、识别文本、错误详情或凭证。
            payload = (state + "\n").encode("ascii")
            if self.process.stdin.write(payload) != len(payload):
                raise BlockingIOError("浮标管道暂时不可写")
        except (OSError, ValueError):
            if not self.warned:
                logger.warning(
                    "状态浮标不可用；语音输入继续运行。可用 indicator-demo 检查"
                )
                self.warned = True
            # 不等待 GUI；关闭故障浮标，避免留下“仍在录音”的错误指示。
            try:
                self.process.terminate()
            except OSError:
                pass

    @property
    def running(self):
        return self.process is not None

    def event_fd(self):
        if self.process is None or self.process.stdout is None:
            return None
        return self.process.stdout.fileno()

    def poll_events(self):
        """非阻塞读取事件行；返回字符串列表，EOF 返回 None。"""
        if self.process is None or self.process.stdout is None:
            return None
        try:
            chunk = os.read(self.process.stdout.fileno(), 65536)
        except BlockingIOError:
            return []
        except OSError:
            return None
        if not chunk:
            return None
        self.event_buffer += chunk
        events = []
        while b"\n" in self.event_buffer:
            line, self.event_buffer = self.event_buffer.split(b"\n", 1)
            text = line.decode("ascii", errors="replace").strip()
            if text.startswith("event:"):
                events.append(text[len("event:"):])
        return events

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()  # EOF 让浮标隐藏并退出。
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
