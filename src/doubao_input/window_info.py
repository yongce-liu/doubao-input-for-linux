"""查询当前焦点窗口，用于按应用选择粘贴快捷键。

终端类应用里 Ctrl+V 是 readline 的 ``quoted-insert``（插入字面字符），
所以粘贴必须换成终端自己的快捷键（多数是 Ctrl+Shift+V，xterm 系是
Shift+Insert）。这里通过 EWMH 的 ``_NET_ACTIVE_WINDOW`` 取到焦点窗口，
再读它的 ``WM_CLASS``。

只用 X11（``xprop``）。Wayland 没有可靠的全局焦点查询方式，返回 None，
由调用方回退到配置的默认快捷键。
"""

from __future__ import annotations

import logging
import os
import re
import subprocess

from doubao_input import session_env
from doubao_input.doubao.host_tools import command_candidates

logger = logging.getLogger(__name__)

# xprop 实测 2~5ms；给 1 秒已足够宽松，同时避免 X 无响应时粘贴被拖住
# （探测失败会回退到 ctrl-v，不影响粘贴本身）。
_TIMEOUT = 1
_QUOTED = re.compile(r'"([^"]*)"')

# 终端类名里足够有辨识度的关键词（不要放 "st" 这类短词，会误伤其它应用）。
_TERMINAL_HINTS = (
    "terminal",
    "konsole",
    "alacritty",
    "kitty",
    "wezterm",
    "ghostty",
    "tabby",
    "hyper",
    "guake",
    "yakuake",
    "tilix",
    "terminator",
    "terminology",
    "qterminal",
    "sakura",
    "roxterm",
    "cool-retro-term",
    "termite",
    "contour",
    "ptyxis",
    "kgx",
    "gnome-console",
    "deepin-terminal",
    "warp-terminal",
)

# 名称太短、做子串匹配容易误伤的终端，只做精确匹配。
_EXACT_TERMINALS = ("foot", "st", "warp", "qterm", "aterm", "eterm", "mintty")

# 这些终端没有 Ctrl+Shift+V，默认粘贴是 Shift+Insert。
_SHIFT_INSERT_CLASSES = ("xterm", "uxterm", "urxvt", "rxvt", "koi8rxvt")


def active_window_class() -> str | None:
    """返回焦点窗口的 WM_CLASS（小写），无法判断时返回 None。"""
    if not _x11_available():
        return None
    window_id = _active_window_id()
    if window_id is None:
        return None
    for command in command_candidates("xprop"):
        try:
            proc = subprocess.run(
                command + ["-id", window_id, "-notype", "WM_CLASS"],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("xprop WM_CLASS 失败: %s", exc)
            continue
        if proc.returncode != 0:
            continue
        names = _QUOTED.findall(proc.stdout)
        if names:
            # 约定取最后一段：("gnome-terminal-server", "Gnome-terminal")
            return names[-1].strip().lower()
    return None


def terminal_shortcut(window_class: str) -> str | None:
    """若该窗口是终端，返回应使用的粘贴快捷键，否则 None。"""
    name = (window_class or "").strip().lower()
    if not name:
        return None
    if name in _SHIFT_INSERT_CLASSES:
        return "shift-insert"
    if name in _EXACT_TERMINALS:
        return "ctrl-shift-v"
    if any(hint in name for hint in _TERMINAL_HINTS):
        return "ctrl-shift-v"
    return None


def _x11_available() -> bool:
    session_env.ensure()
    if os.environ.get("DISPLAY"):
        return True
    # 纯 Wayland（没有 XWayland）时无法查询焦点窗口。
    return False


def _active_window_id() -> str | None:
    for command in command_candidates("xprop"):
        try:
            proc = subprocess.run(
                command + ["-root", "-notype", "_NET_ACTIVE_WINDOW"],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("xprop 焦点窗口失败: %s", exc)
            continue
        if proc.returncode != 0:
            continue
        match = re.search(r"0x[0-9a-fA-F]+", proc.stdout)
        if match and int(match.group(0), 16) != 0:
            return match.group(0)
    return None
