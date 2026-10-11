"""Global PTT trigger via evdev.

Listens for right-Ctrl key-down / key-up on every accessible keyboard
device under /dev/input/event*. Forwards edges through an injected scheduler
(asyncio for the service).

We do NOT grab the keyboard (EVIOCGRAB), so the right-Ctrl event is
still passed to the foreground application — that is acceptable
because right-Ctrl alone is harmless in nearly every app.

Multi-keyboard and hot-plug: device list is re-scanned periodically
(once per second) so that swapping a keyboard while running does not
leave us listening on a stale fd.
"""

from __future__ import annotations

import json
import logging
import os
import select
import struct
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

EV_KEY = 0x01
KEY_RIGHTCTRL = 97  # linux/input-event-codes.h

# 可选触发键：名称 -> evdev 键码（linux/input-event-codes.h）。
HOTKEYS = {
    "right-ctrl": ("右 Ctrl", 97),
    "right-shift": ("右 Shift", 54),
    "right-alt": ("右 Alt", 100),
}
DEFAULT_HOTKEY = "right-ctrl"


def load_hotkey() -> str:
    from doubao_input.doubao.config import get_trigger_config_path

    try:
        name = json.loads(get_trigger_config_path().read_text()).get("key")
    except (OSError, ValueError):
        return DEFAULT_HOTKEY
    return name if name in HOTKEYS else DEFAULT_HOTKEY


def save_hotkey(name: str) -> None:
    from doubao_input.doubao.config import get_trigger_config_path

    if name not in HOTKEYS:
        raise ValueError(f"未知快捷键: {name}")
    get_trigger_config_path().write_text(json.dumps({"key": name}))
# struct input_event on 64-bit: long long tv_sec; long tv_usec; unsigned short type; unsigned short code; int value;
_EVENT_SIZE = 24


class EvdevPtt:
    """Right-Ctrl push-to-talk listener.

    Callbacks (invoked through the selected main-loop scheduler):
      on_press():   right-Ctrl pressed down
      on_release(): right-Ctrl released
    """

    def __init__(
        self,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        on_error: Callable[[str], None] | None = None,
        dispatch=None,
        key_code: int = KEY_RIGHTCTRL,
    ) -> None:
        self._key_code = key_code
        self._on_press = on_press
        self._on_release = on_release
        self._on_error = on_error
        self._dispatch_callback = dispatch
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._fds: list[int] = []
        self._paths: list[str] = []
        self._rescan_interval = 1.0
        self._last_rescan = 0.0

    # ---- public ----

    def start(self) -> bool:
        """Start the listener thread. Returns False if no accessible devices."""
        available = self._scan()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="evdev-ptt", daemon=True)
        self._thread.start()
        logger.info("EvdevPtt started on %d device(s)", len(self._fds))
        return available

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._close_fds()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---- internals ----

    def _scan(self) -> bool:
        """Open every /dev/input/event* that we can read AND that supports KEY_RIGHTCTRL."""
        import evdev  # type: ignore

        previous = dict(zip(self._paths, self._fds))
        fds: list[int] = []
        paths: list[str] = []
        # evdev.list_devices() returns paths the process can actually open.
        for path in evdev.list_devices():
            try:
                if path in previous:
                    fds.append(previous.pop(path))
                    paths.append(path)
                    continue
                dev = evdev.InputDevice(path)
                try:
                    caps = dev.capabilities()
                finally:
                    dev.close()
                if EV_KEY in caps and self._key_code in caps[EV_KEY]:
                    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                    fds.append(fd)
                    paths.append(path)
            except (PermissionError, FileNotFoundError, OSError) as e:
                logger.debug("Skip %s: %s", path, e)
                continue
            except Exception as e:
                logger.debug("Skip %s: %s", path, e)
                continue
        for fd in previous.values():
            os.close(fd)
        self._fds = fds
        self._paths = paths
        return bool(fds)

    def _close_fds(self) -> None:
        for fd in self._fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds = []
        self._paths = []

    def _run(self) -> None:
        dispatch = self._dispatch_callback
        if dispatch is None:
            from gi.repository import GLib

            dispatch = GLib.idle_add

        buf = b""
        try:
            while not self._stop.is_set():
                # periodic rescan for hot-plug
                now = time.monotonic()
                if now - self._last_rescan > self._rescan_interval:
                    self._last_rescan = now
                    if not self._scan():
                        self._notify_error("无可访问的键盘设备；请检查 input 组权限")
                        self._stop.wait(2.0)
                        continue

                if not self._fds:
                    time.sleep(0.2)
                    continue

                try:
                    rlist, _, _ = select.select(self._fds, [], [], 0.5)
                except (OSError, ValueError):
                    # Some fd was closed by the kernel (device unplugged). Re-scan.
                    self._close_fds()
                    continue
                for fd in rlist:
                    try:
                        data = os.read(fd, _EVENT_SIZE * 16)
                    except OSError:
                        self._close_fds()
                        dispatch(self._on_release)
                        break
                    if not data:
                        self._close_fds()
                        dispatch(self._on_release)
                        break
                    buf = data
                    self._dispatch(buf, dispatch)
        except Exception as e:  # pragma: no cover
            logger.exception("EvdevPtt crashed: %s", e)
            self._notify_error(f"evdev 监听崩溃: {e}")

    def _dispatch(self, data: bytes, idle_add) -> None:
        i = 0
        n = len(data)
        # Cache right-ctrl down state per "logical session" (across all fds).
        # We treat the down edge simply as "any fd just got a press",
        # because the kernel delivers only one of them to userspace readers
        # per real keypress.
        while i + _EVENT_SIZE <= n:
            tv_sec, tv_usec, ev_type, ev_code, ev_value = struct.unpack(
                "llHHi", data[i : i + _EVENT_SIZE]
            )
            i += _EVENT_SIZE
            if ev_type != EV_KEY or ev_code != self._key_code:
                continue
            if ev_value == 1:  # press
                try:
                    idle_add(self._on_press)
                except Exception:
                    pass
            elif ev_value == 0:  # release
                try:
                    idle_add(self._on_release)
                except Exception:
                    pass
            # ev_value == 2 (autorepeat) is intentionally ignored

    def _notify_error(self, msg: str) -> None:
        logger.warning(msg)
        if self._on_error:
            try:
                dispatch = self._dispatch_callback
                if dispatch is None:
                    from gi.repository import GLib

                    dispatch = GLib.idle_add
                dispatch(self._on_error, msg)
            except Exception:
                pass
