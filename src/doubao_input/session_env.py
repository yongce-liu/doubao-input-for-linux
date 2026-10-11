"""运行时解析桌面显示会话变量。

systemd 用户服务在登录早期就被拉起，那时桌面会话还没把 ``DISPLAY`` /
``XAUTHORITY`` / ``WAYLAND_DISPLAY`` 导入 systemd 用户管理器；而且进程环境
是启动瞬间的快照，之后管理器环境变了也不会自动同步。语音粘贴
（``xclip`` / ``wl-copy``）和托盘图标都依赖这些变量，缺了就会出现
"Can't open display: (null)" 或图标静默消失。

因此不依赖继承的环境，运行时主动解析一次并写回 ``os.environ``：

1. 自身环境已有则直接用；
2. ``loginctl``：权威来源，普通用户即可查询自己的会话；
3. ``systemctl --user show-environment``：桌面会话稍后导入的值；
4. ``/tmp/.X11-unix/X<n>`` socket 探测：最后的兜底。

只补空值，不覆盖调用方已有的设置。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT = 3
_KEYS = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_SESSION_TYPE")
_lock = threading.Lock()


def has_display() -> bool:
    """当前是否已有可用的显示会话标识。"""
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ensure() -> bool:
    """尽量补齐显示会话变量；返回此刻是否可用。"""
    if has_display():
        return True
    with _lock:
        if has_display():
            return True
        found = _detect()
        for key in _KEYS:
            value = found.get(key)
            if value and not os.environ.get(key):
                os.environ[key] = value
        if has_display():
            logger.info(
                "已解析桌面会话 DISPLAY=%s WAYLAND_DISPLAY=%s 类型=%s",
                os.environ.get("DISPLAY") or "-",
                os.environ.get("WAYLAND_DISPLAY") or "-",
                os.environ.get("XDG_SESSION_TYPE") or "-",
            )
            return True
        return False


def _detect() -> dict[str, str]:
    found: dict[str, str] = {}
    for source in (_from_loginctl, _from_systemd_user_env, _from_x11_socket):
        try:
            found.update(source())
        except Exception as exc:  # noqa: BLE001 -- 环境探测失败不应影响语音核心
            logger.debug("会话探测 %s 失败: %s", source.__name__, exc)
        if found.get("DISPLAY") or found.get("WAYLAND_DISPLAY"):
            break
    if found.get("DISPLAY") and not found.get("XAUTHORITY"):
        authority = Path.home() / ".Xauthority"
        if authority.exists():
            found["XAUTHORITY"] = str(authority)
    return found


def _run(argv: list[str]) -> str:
    if not shutil.which(argv[0]):
        return ""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _own_session_ids() -> list[str]:
    """当前用户的所有会话 id；优先使用 XDG_SESSION_ID。"""
    output = _run(["loginctl", "list-sessions", "--no-legend"])
    uid = str(os.getuid())
    ids = [
        parts[0]
        for parts in (line.split() for line in output.splitlines())
        if len(parts) >= 2 and parts[1] == uid
    ]
    preferred = os.environ.get("XDG_SESSION_ID")
    if preferred in ids:
        ids.remove(preferred)
        ids.insert(0, preferred)
    return ids


def _from_loginctl() -> dict[str, str]:
    for session_id in _own_session_ids():
        props = _session_properties(session_id)
        if props.get("Active") != "yes":
            continue
        found = _env_from_session(props)
        if found:
            return found
    return {}


def _session_properties(session_id: str) -> dict[str, str]:
    output = _run(
        [
            "loginctl",
            "show-session",
            session_id,
            "-p",
            "Display",
            "-p",
            "Type",
            "-p",
            "Active",
        ]
    )
    props: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def _env_from_session(props: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    session_type = props.get("Type", "")
    display = props.get("Display", "")
    if session_type:
        found["XDG_SESSION_TYPE"] = session_type
    if session_type == "wayland":
        wayland = _wayland_socket()
        if wayland:
            found["WAYLAND_DISPLAY"] = wayland
    if display and (session_type == "x11" or not found.get("WAYLAND_DISPLAY")):
        found["DISPLAY"] = display
    return found


def _wayland_socket() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        return ""
    for number in range(4):
        name = f"wayland-{number}"
        if Path(runtime, name).exists():
            return name
    return ""


def _from_systemd_user_env() -> dict[str, str]:
    output = _run(["systemctl", "--user", "show-environment"])
    found: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            if key in _KEYS and value:
                found[key] = value
    return found


def _from_x11_socket() -> dict[str, str]:
    sockets = sorted(Path("/tmp/.X11-unix").glob("X*"))
    for socket in sockets:
        suffix = socket.name[1:]
        if suffix.isdigit():
            return {"DISPLAY": f":{suffix}", "XDG_SESSION_TYPE": "x11"}
    return {}
