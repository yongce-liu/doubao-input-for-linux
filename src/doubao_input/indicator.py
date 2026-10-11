"""独立反馈进程：X11 Dock/托盘状态图标（或右下角浮标），Wayland 系统通知。"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys

logger = logging.getLogger(__name__)


def visual_state(state):
    if state in ("connecting", "recording"):
        # connecting 阶段录音已经启动，音频暂存在发送队列中。
        return "recording"
    if state in ("finishing", "pasting"):
        return "processing"
    if state in ("error", "login-required"):
        return state
    if state == "idle":
        return "idle"
    return None  # stopped 及其它：隐藏


def bottom_right(area, width, height, margin=24):
    return (
        max(area.x, area.x + area.width - width - margin),
        max(area.y, area.y + area.height - height - margin),
    )


class IndicatorController:
    def __init__(self, backend, scheduler):
        self.backend = backend
        self.scheduler = scheduler
        self.previous = None
        self.timer = None

    def update(self, state):
        view = visual_state(state)
        if view == self.previous:
            return
        self.previous = view
        if self.timer is not None:
            self.scheduler.source_remove(self.timer)
            self.timer = None
        if view is None:
            self.backend.hide()
            return
        # 常驻 Dock 图标后端也显示待机态；浮标/通知后端对待机态保持隐藏。
        if view == "idle" and not self.backend.shows_idle:
            self.backend.hide()
            return
        self.backend.show(view)
        if view in ("error", "login-required") and self.backend.auto_hide_error:
            self.timer = self.scheduler.timeout_add(2500, self.expire)

    def expire(self):
        self.timer = None
        self.previous = None
        self.backend.hide()
        return False

    def close(self):
        if self.timer is not None:
            self.scheduler.source_remove(self.timer)
            self.timer = None
        self.backend.hide()


class DockStatusIcon:
    """系统托盘（Dock）常驻图标：待机灰、录音绿、处理黄、错误红。

    基于 Gtk.StatusIcon（XEmbed），Mint Cinnamon / X11 面板可直接显示。
    """

    shows_idle = True
    auto_hide_error = False

    def __init__(self, emit=None):
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_foreign("cairo")
        import cairo
        from gi.repository import Gdk, Gtk

        from doubao_input.indicator_art import (
            SIZE,
            STATE_COLORS,
            STATE_LABELS,
            draw_badge,
        )

        ok, _argv = Gtk.init_check([])
        if not ok:
            raise RuntimeError("无法连接 X11 显示会话")
        self.Gtk = Gtk
        self.emit = emit or (lambda event: None)
        self.pixbufs = {}
        for state in STATE_COLORS:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
            draw_badge(cairo.Context(surface), state)
            self.pixbufs[state] = Gdk.pixbuf_get_from_surface(surface, 0, 0, SIZE, SIZE)
        self.labels = STATE_LABELS
        self.icon = Gtk.StatusIcon()
        self.icon.set_visible(True)
        # 左键点击 = 按下/松开 Ctrl；右键 = 菜单。
        self.icon.connect("activate", self._on_activate)
        self.icon.connect("popup-menu", self._on_popup_menu)
        self.show("idle")

    def show(self, state):
        self.icon.set_from_pixbuf(self.pixbufs[state])
        self.icon.set_tooltip_text(self.labels.get(state, "豆包语音输入"))

    def hide(self):
        self.icon.set_visible(False)

    def _on_activate(self, _icon):
        self.emit("toggle")

    def _on_popup_menu(self, icon, button, activate_time):
        menu = self._build_menu()
        menu.popup(
            None, None, self.Gtk.StatusIcon.position_menu, self.icon,
            button, activate_time,
        )
        return True

    def _radio(self, group, label, active, event):
        item = self.Gtk.RadioMenuItem(group=group, label=label)
        item.set_active(active)
        item.connect("activate", lambda *_: self.emit(event))
        return item

    def _build_menu(self):
        Gtk = self.Gtk
        menu = Gtk.Menu()

        # 麦克风选择（展开菜单时实时枚举，勾选当前配置）
        from doubao_input.doubao.audio_capture import AudioCapture
        from doubao_input.doubao.config import get_audio_config_path
        from doubao_input.trigger.evdev_ptt import HOTKEYS, load_hotkey

        try:
            current_device = json.loads(get_audio_config_path().read_text()).get(
                "device"
            )
        except (OSError, ValueError):
            current_device = None
        mic_item = Gtk.MenuItem(label="麦克风选择")
        mic_menu = Gtk.Menu()
        group = None
        group = self._radio(group, "自动（系统默认）", current_device is None, "mic:auto")
        mic_menu.append(group)
        try:
            devices = AudioCapture.list_input_devices()
        except Exception:  # noqa: BLE001 -- 菜单边界：枚举失败只显示自动项
            devices = []
        for device in devices:
            index = device["index"]
            entry = self._radio(
                group,
                f"{index}: {device['name']}",
                current_device == index,
                f"mic:{index}",
            )
            mic_menu.append(entry)
        mic_item.set_submenu(mic_menu)
        menu.append(mic_item)

        # 快捷键选择
        current_key = load_hotkey()
        key_item = Gtk.MenuItem(label="快捷键选择")
        key_menu = Gtk.Menu()
        group = None
        for name, (label, _code) in HOTKEYS.items():
            group = self._radio(group, label, current_key == name, f"hotkey:{name}")
            key_menu.append(group)
        key_item.set_submenu(key_menu)
        menu.append(key_item)

        menu.append(Gtk.SeparatorMenuItem())
        login = Gtk.MenuItem(label="登录…")
        login.connect("activate", lambda *_: self.emit("login"))
        menu.append(login)
        quit_item = Gtk.MenuItem(label="退出")
        quit_item.connect("activate", lambda *_: self.emit("quit"))
        menu.append(quit_item)
        menu.show_all()
        return menu


class X11Badge:
    """右下角浮标（可选样式）：录音时显示，待机隐藏。"""

    shows_idle = False
    auto_hide_error = True

    def __init__(self):
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_foreign("cairo")
        import cairo
        from gi.repository import Gdk, Gtk

        from doubao_input.indicator_art import SIZE

        ok, _argv = Gtk.init_check([])
        if not ok:
            raise RuntimeError("无法连接 X11 显示会话")
        self.Gdk, self.Gtk, self.cairo = Gdk, Gtk, cairo
        self.state = "recording"
        self.window = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.window.set_title("豆包麦克风状态")
        self.window.set_default_size(SIZE, SIZE)
        # 无子组件的 GTK popup 会采用 200px 自然尺寸，必须明确内容请求尺寸。
        canvas = Gtk.DrawingArea()
        canvas.set_size_request(SIZE, SIZE)
        self.window.add(canvas)
        self.window.set_resizable(False)
        self.window.set_decorated(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_can_focus(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_above(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.window.set_app_paintable(True)
        screen = self.window.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.window.set_visual(visual)
        self.window.connect("draw", self.draw)
        self.window.connect("realize", self.realized)

    def realized(self, window):
        # 空输入区域：浮标不截获鼠标，也不妨碍其下方输入框。
        window.get_window().input_shape_combine_region(self.cairo.Region(), 0, 0)

    def draw(self, window, cr):
        from doubao_input.indicator_art import draw_badge

        cr.set_operator(self.cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(self.cairo.OPERATOR_OVER)
        draw_badge(
            cr, self.state, window.get_allocated_width(), window.get_allocated_height()
        )
        return True

    def show(self, state):
        from doubao_input.indicator_art import SIZE

        self.state = state
        display = self.Gdk.Display.get_default()
        pointer = display.get_default_seat().get_pointer()
        _screen, x, y = pointer.get_position()
        monitor = display.get_monitor_at_point(x, y) or display.get_primary_monitor()
        if monitor is not None:
            self.window.move(*bottom_right(monitor.get_workarea(), SIZE, SIZE))
        # 不使用 present()/grab_focus()，避免抢走正在输入的应用焦点。
        self.window.show_all()
        self.window.queue_draw()

    def hide(self):
        self.window.hide()


class NotificationBadge:
    """Wayland 不强制定位窗口：由桌面通知服务显示，不创建可聚焦主窗口。"""

    shows_idle = False
    auto_hide_error = True

    def __init__(self):
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None,
            "org.freedesktop.Notifications",
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            None,
        )
        self.id = 0

    def show(self, state):
        text = {
            "recording": "麦克风正在录音",
            "processing": "正在处理语音",
            "error": "语音输入未完成，请检查服务状态",
            "login-required": "请运行 doubao-input login 登录",
        }[state]
        hints = {
            "transient": self.GLib.Variant("b", True),
            "urgency": self.GLib.Variant("y", 1),
        }
        timeout = 2500 if state in ("error", "login-required") else 0
        reply = self.proxy.call_sync(
            "Notify",
            self.GLib.Variant(
                "(susssasa{sv}i)",
                (
                    "豆包语音输入",
                    self.id,
                    "audio-input-microphone",
                    text,
                    "",
                    [],
                    hints,
                    timeout,
                ),
            ),
            self.Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
        self.id = reply.unpack()[0]

    def hide(self):
        if self.id:
            try:
                self.proxy.call_sync(
                    "CloseNotification",
                    self.GLib.Variant("(u)", (self.id,)),
                    self.Gio.DBusCallFlags.NONE,
                    1000,
                    None,
                )
            finally:
                self.id = 0


def main(*, demo=False, style="dock"):
    from gi.repository import GLib

    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or (
        os.environ.get("WAYLAND_DISPLAY")
        and os.environ.get("XDG_SESSION_TYPE") != "x11"
    )
    def emit(event):
        try:
            sys.stdout.write(f"event:{event}\n")
            sys.stdout.flush()
        except OSError:
            loop.quit()

    if os.environ.get("DISPLAY") and not wayland:
        os.environ["GDK_BACKEND"] = "x11"
        backend_class = DockStatusIcon if style == "dock" else X11Badge
        try:
            backend = backend_class(emit=emit) if backend_class is DockStatusIcon else backend_class()
        except (ImportError, ValueError, RuntimeError) as exc:
            logger.warning("状态图标无法初始化，改用系统通知: %s", exc)
            backend = NotificationBadge()
    else:
        backend = NotificationBadge()
    controller = IndicatorController(backend, GLib)
    loop = GLib.MainLoop()
    buffer = b""

    def stop():
        loop.quit()
        return False

    def receive(fd, condition):
        nonlocal buffer
        chunk = os.read(fd, 4096)
        if not chunk:
            return stop()
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            try:
                controller.update(line.decode("ascii", errors="replace"))
            except Exception:
                logger.exception("状态浮标更新失败")
                return stop()
        return True

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, stop)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, stop)
    if demo:
        # 依次展示：待机灰 → 录音绿 → 处理黄 → 错误红 → 待机灰
        for i, state in enumerate(("idle", "recording", "finishing", "error", "idle")):
            GLib.timeout_add(
                500 + 1200 * i, lambda s=state: (controller.update(s), False)[1]
            )
        GLib.timeout_add(500 + 1200 * 5, stop)
    else:
        GLib.io_add_watch(
            sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, receive
        )
    try:
        loop.run()
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    import argparse

    parser_ = argparse.ArgumentParser(
        description="豆包语音输入状态图标（内部辅助进程）"
    )
    parser_.add_argument("--style", choices=["dock", "float"], default="dock")
    parser_.add_argument("--demo", action="store_true")
    parsed = parser_.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        raise SystemExit(main(demo=parsed.demo, style=parsed.style))
    except Exception:
        logger.exception("麦克风状态图标已退出")
        raise SystemExit(1) from None
