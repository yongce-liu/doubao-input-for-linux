"""Text injection: copy-to-clipboard + simulate Ctrl+V via uinput.

The only reliable way to "paste" into a native Wayland app on GNOME
(Mutter does not implement the virtual-keyboard protocol that wtype
needs) is to write text to the clipboard and then synthesize a
Ctrl+V keypress via a /dev/uinput virtual keyboard.

CJK text cannot be typed key-by-key through a virtual keyboard, so
the clipboard path is mandatory for Chinese.

The uinput device is opened lazily on first inject() and kept alive
for the process lifetime to avoid the per-injection cost of creating
and destroying a kernel device.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

from doubao_input import session_env, window_info
from doubao_input.doubao.host_tools import command_candidates

logger = logging.getLogger(__name__)

# Pause after wl-copy so the clipboard manager has settled.
# Also pause after the right-Ctrl physical release to avoid mixing it
# with our injected Left Ctrl.
PASTE_DELAY = 0.08  # seconds

# Linux keycodes (from linux/input-event-codes.h)
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_V = 47
KEY_INSERT = 110

# 支持的粘贴快捷键。auto 表示按焦点窗口自动选择：
# 终端用终端自己的快捷键，其它应用用 Ctrl+V。
PASTE_SHORTCUTS = ("auto", "ctrl-v", "ctrl-shift-v", "shift-insert")

# 快捷键 -> (是否按 Ctrl, 是否按 Shift, 主键码)
_SHORTCUT_KEYS = {
    "ctrl-v": (True, False, KEY_V),
    "ctrl-shift-v": (True, True, KEY_V),
    "shift-insert": (False, True, KEY_INSERT),
}


class Injector:
    """Inject text into the currently-focused input field."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ui = None  # evdev.UInput

    # ---- public ----

    def inject(self, text: str, shortcut: str = "auto") -> bool:
        """Write text to the clipboard, then synthesize the paste shortcut."""
        if not text:
            return False
        with self._lock:
            # 焦点窗口必须在写剪贴板之前判断：写剪贴板本身不影响焦点，
            # 但提前判断可以让日志里记录的是真正生效的快捷键。
            resolved = self.resolve_shortcut(shortcut)
            ok_copy = self._copy_to_clipboard(text)
            if not ok_copy:
                logger.error("clipboard copy failed; cannot inject")
                return False
            time.sleep(PASTE_DELAY)
            return self._simulate_paste(resolved)

    def resolve_shortcut(self, shortcut: str = "auto") -> str:
        """把 auto 解析成具体快捷键；非 auto 的原样返回。"""
        if shortcut not in PASTE_SHORTCUTS:
            logger.warning("未知粘贴快捷键 %r，改用 ctrl-v", shortcut)
            return "ctrl-v"
        if shortcut != "auto":
            return shortcut
        try:
            window_class = window_info.active_window_class()
        except Exception as exc:  # noqa: BLE001 -- 焦点探测失败不应阻断粘贴
            logger.debug("焦点窗口探测失败: %s", exc)
            return "ctrl-v"
        terminal_shortcut = window_info.terminal_shortcut(window_class or "")
        if terminal_shortcut:
            logger.info(
                "焦点窗口 %s 是终端，使用 %s 粘贴", window_class, terminal_shortcut
            )
            return terminal_shortcut
        return "ctrl-v"

    def close(self) -> None:
        with self._lock:
            if self._ui is not None:
                try:
                    self._ui.close()
                except Exception:
                    pass
                self._ui = None

    # ---- internals ----

    def _copy_to_clipboard(self, text: str) -> bool:
        # 服务由 systemd 启动时环境里可能没有 DISPLAY，剪贴板工具会报
        # "Can't open display: (null)"；这里按需解析一次。
        session_env.ensure()
        data = text.encode("utf-8")
        # wl-copy first
        for cmd in (
            command_candidates("wl-copy") if os.environ.get("WAYLAND_DISPLAY") else []
        ):
            try:
                subprocess.run(cmd, input=data, check=True, timeout=3)
                logger.info("clipboard: wl-copy ok")
                return True
            except Exception as e:
                logger.debug("wl-copy failed: %s", e)
        # xclip fallback (XWayland only)
        for cmd in command_candidates("xclip"):
            try:
                subprocess.run(
                    cmd + ["-selection", "clipboard"],
                    input=data,
                    check=True,
                    timeout=3,
                )
                logger.info("clipboard: xclip ok")
                return True
            except Exception as e:
                logger.debug("xclip failed: %s", e)
        return False

    def _get_uinput(self):
        if self._ui is not None:
            return self._ui
        import evdev  # type: ignore

        # IMPORTANT: declare every keycode we may ever emit, INCLUDING
        # KEY_LEFTSHIFT. The uinput protocol silently drops events for
        # keycodes that were not advertised at device-create time. If
        # LEAVING_SHIFT_OUT, the Ctrl+Shift+V path degenerates into
        # Ctrl+V — terminals then swallow it as VLNEXT (^V) and paste
        # never happens.
        self._ui = evdev.UInput(
            events={
                evdev.ecodes.EV_KEY: [
                    KEY_LEFTCTRL,
                    KEY_LEFTSHIFT,
                    KEY_V,
                    KEY_INSERT,
                ],
            },
            name="doubao-input-virtual-kbd",
        )
        logger.info("uinput virtual keyboard created")
        return self._ui

    def _simulate_paste(self, shortcut: str = "ctrl-v") -> bool:
        """Send the paste shortcut through uinput."""
        ctrl, shift, key = _SHORTCUT_KEYS.get(
            shortcut, _SHORTCUT_KEYS["ctrl-v"]
        )
        try:
            ui = self._get_uinput()
            import time as _t

            import evdev  # type: ignore

            # Some receivers (notably GTK apps and terminals) need
            # measurable time between key events; otherwise the
            # press-release sequence gets coalesced into nothing and
            # the paste shortcut never registers.
            GAP = 0.012  # seconds between events

            def emit(code: int, value: int) -> None:
                ui.write(evdev.ecodes.EV_KEY, code, value)
                ui.syn()
                _t.sleep(GAP)

            # Press
            if ctrl:
                emit(KEY_LEFTCTRL, 1)
            if shift:
                emit(KEY_LEFTSHIFT, 1)
            emit(key, 1)
            _t.sleep(GAP)
            # Release（逆序释放，避免应用把中间态当成其他组合键）
            emit(key, 0)
            if shift:
                emit(KEY_LEFTSHIFT, 0)
            if ctrl:
                emit(KEY_LEFTCTRL, 0)
            logger.info(
                "paste: uinput ok (shortcut=%s, gap=%.0fms)", shortcut, GAP * 1000
            )
            return True
        except Exception as e:
            logger.error("uinput paste failed: %s", e)
            return False
