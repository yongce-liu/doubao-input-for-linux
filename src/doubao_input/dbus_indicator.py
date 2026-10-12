"""StatusNotifierItem / DBusMenu 与系统通知，不依赖 GTK。"""
# ruff: noqa: F821 -- D-Bus 注解是协议签名，不是 Python 类型。

import asyncio
import json
import logging
import signal
import sys
from contextlib import suppress

from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.errors import DBusError
from dbus_next.service import (
    ServiceInterface,
    dbus_property,
    method,
)
from dbus_next.service import (
    signal as dbus_signal,
)

LABELS = {
    "idle": "豆包语音输入",
    "recording": "正在录音",
    "processing": "正在识别",
    "error": "识别失败",
    "login-required": "请先登录",
}
COLORS = {
    "idle": (130, 140, 150),
    "recording": (40, 190, 100),
    "processing": (240, 180, 40),
    "error": (225, 65, 65),
    "login-required": (225, 65, 65),
}


def visual_state(state):
    if state in ("connecting", "recording"):
        return "recording"
    if state in ("finishing", "pasting"):
        return "processing"
    if state in ("error", "login-required", "idle"):
        return state
    return None


def icon_pixels(state):
    color = COLORS.get(state, COLORS["idle"])
    pixels = bytearray()
    for y in range(32):
        for x in range(32):
            inside = (x - 15.5) ** 2 + (y - 15.5) ** 2 <= 225
            mic = (
                (13 <= x <= 18 and 7 <= y <= 19)
                or (15 <= x <= 16 and 19 <= y <= 25)
                or (11 <= x <= 20 and y == 25)
            )
            pixels.extend(
                (255, *((255, 255, 255) if mic else color)) if inside else (0, 0, 0, 0)
            )
    return bytes(pixels)


class Menu(ServiceInterface):
    def __init__(self, emit):
        super().__init__("com.canonical.dbusmenu")
        self.emit = emit
        self.revision = 0
        self.actions = {}
        self.nodes = {}
        self.rebuild()

    def rebuild(self):
        from doubao_input.doubao.audio_capture import AudioCapture
        from doubao_input.doubao.config import get_audio_config_path
        from doubao_input.trigger.evdev_ptt import HOTKEYS, load_hotkey

        self.actions = {}
        self.nodes = {}

        def node(label, action=None, children=None, checked=None):
            ident = len(self.nodes) + 1
            props = {
                "label": Variant("s", label),
                "enabled": Variant("b", True),
                "visible": Variant("b", True),
            }
            if checked is not None:
                props.update(
                    {
                        "toggle-type": Variant("s", "radio"),
                        "toggle-state": Variant("i", int(checked)),
                    }
                )
            if children is not None:
                props["children-display"] = Variant("s", "submenu")
            result = [ident, props, children or []]
            self.nodes[ident] = result
            if action:
                self.actions[ident] = action
            return Variant("(ia{sv}av)", result)

        try:
            current = json.loads(get_audio_config_path().read_text()).get("device")
        except (OSError, ValueError):
            current = None
        microphones = [node("自动（系统默认）", "mic:auto", checked=current is None)]
        try:
            devices = AudioCapture.list_input_devices()
        except Exception:  # noqa: BLE001 -- 枚举失败不影响菜单其它功能
            devices = []
        for device in devices:
            idx = device["index"]
            microphones.append(
                node(f"{idx}: {device['name']}", f"mic:{idx}", checked=current == idx)
            )
        mic_menu = node("麦克风选择", children=microphones)
        hotkey = load_hotkey()
        keys = [
            node(label, f"hotkey:{name}", checked=hotkey == name)
            for name, (label, _) in HOTKEYS.items()
        ]
        self.root = [
            0,
            {"children-display": Variant("s", "submenu")},
            [
                node("开始 / 停止录音", "toggle"),
                mic_menu,
                node("快捷键选择", children=keys),
                node("重新登录…", "login"),
                node("退出", "quit"),
            ],
        ]
        self.nodes[0] = self.root
        self.revision += 1

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "u":
        return 3

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> "s":
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "as":
        return []

    @method()
    def GetLayout(self, parent: "i", depth: "i", names: "as") -> "u(ia{sv}av)":
        def layout(item, remaining):
            ident, props, children = item
            return [
                ident,
                {k: v for k, v in props.items() if not names or k in names},
                []
                if remaining == 0
                else [
                    Variant("(ia{sv}av)", layout(c.value, remaining - 1))
                    for c in children
                ],
            ]

        return [self.revision, layout(self.nodes.get(parent, self.root), depth)]

    @method()
    def GetGroupProperties(self, ids: "ai", names: "as") -> "a(ia{sv})":
        return [
            [i, {k: v for k, v in self.nodes[i][1].items() if not names or k in names}]
            for i in (ids or list(self.nodes))
            if i in self.nodes
        ]

    @method()
    def GetProperty(self, ident: "i", name: "s") -> "v":
        return self.nodes[ident][1][name]

    @method()
    def AboutToShow(self, ident: "i") -> "b":
        if ident == 0:
            self.rebuild()
            self.LayoutUpdated(self.revision, 0)
            return True
        return False

    @method()
    def AboutToShowGroup(self, ids: "ai") -> "aiai":
        if 0 in ids:
            self.rebuild()
            self.LayoutUpdated(self.revision, 0)
            return [[0], []]
        return [[], []]

    @method()
    def Event(self, ident: "i", event: "s", data: "v", timestamp: "u"):
        if event == "clicked" and ident in self.actions:
            self.emit(self.actions[ident])

    @method()
    def EventGroup(self, events: "a(isvu)") -> "ai":
        for event in events:
            self.Event(*event)
        return []

    @dbus_signal()
    def LayoutUpdated(self, revision, parent) -> "ui":
        return [revision, parent]


class Tray(ServiceInterface):
    def __init__(self, emit):
        super().__init__("org.kde.StatusNotifierItem")
        self.emit = emit
        self.state = "idle"

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "doubao-input"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return LABELS.get(self.state, LABELS["idle"])

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active" if self.state in LABELS else "Passive"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "u":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> "a(iiay)":
        return [[32, 32, icon_pixels(self.state)]]

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return ["", self.IconPixmap, self.Title, "点击开始 / 停止录音"]

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return "/MenuBar"

    @method()
    def Activate(self, x: "i", y: "i"):
        self.emit("toggle")

    @method()
    def SecondaryActivate(self, x: "i", y: "i"):
        self.emit("toggle")

    @method()
    def ContextMenu(self, x: "i", y: "i"):
        pass  # 宿主通过 Menu 属性读取 DBusMenu。

    @method()
    def Scroll(self, delta: "i", orientation: "s"):
        pass

    @dbus_signal()
    def NewIcon(self):
        pass

    @dbus_signal()
    def NewToolTip(self):
        pass

    @dbus_signal()
    def NewTitle(self):
        pass

    @dbus_signal()
    def NewStatus(self, status) -> "s":
        return status

    def update(self, state):
        self.state = state
        self.emit_properties_changed(
            {
                "IconPixmap": self.IconPixmap,
                "Title": self.Title,
                "ToolTip": self.ToolTip,
                "Status": self.Status,
            }
        )
        self.NewIcon()
        self.NewToolTip()
        self.NewTitle()
        self.NewStatus(self.Status)


async def run(*, demo=False, style="dock"):
    bus = await MessageBus().connect()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def emit(event):
        try:
            print(f"event:{event}", flush=True)
        except OSError:
            stop.set()

    tray = Tray(emit)
    bus.export("/StatusNotifierItem", tray)
    bus.export("/MenuBar", Menu(emit))
    notifications = None
    notification_id = 0
    registered = False
    reader = asyncio.StreamReader()
    transport = None
    try:
        if style == "dock":
            try:
                introspection = await bus.introspect(
                    "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher"
                )
                watcher = bus.get_proxy_object(
                    "org.kde.StatusNotifierWatcher",
                    "/StatusNotifierWatcher",
                    introspection,
                ).get_interface("org.kde.StatusNotifierWatcher")
                await watcher.call_register_status_notifier_item("/StatusNotifierItem")
                registered = True
            except (DBusError, OSError):
                logging.getLogger(__name__).info(
                    "桌面无 StatusNotifier 托盘，使用系统通知"
                )
        if not registered:
            intro = await bus.introspect(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications"
            )
            notifications = bus.get_proxy_object(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications", intro
            ).get_interface("org.freedesktop.Notifications")

        async def update(raw):
            nonlocal notification_id
            state = visual_state(raw)
            tray.update(state)
            if notifications:
                if state in (None, "idle"):
                    if notification_id:
                        await notifications.call_close_notification(notification_id)
                        notification_id = 0
                else:
                    notification_id = await notifications.call_notify(
                        "豆包语音输入",
                        notification_id,
                        "audio-input-microphone",
                        LABELS[state],
                        "",
                        [],
                        {"transient": Variant("b", True)},
                        2500 if state in ("error", "login-required") else 0,
                    )

        async def receive():
            if demo:
                for state in ("idle", "recording", "finishing", "error", "idle"):
                    await update(state)
                    await asyncio.sleep(1.2)
            else:
                while line := await reader.readline():
                    await update(line.decode("ascii", errors="replace").strip())

        if not demo:
            transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
            )
        task = asyncio.create_task(receive())
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                [task, stopping], return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                task.result()
        finally:
            task.cancel()
            stopping.cancel()
            await asyncio.gather(task, stopping, return_exceptions=True)
    finally:
        if transport:
            transport.close()
        if notifications and notification_id:
            with suppress(DBusError, OSError):
                await notifications.call_close_notification(notification_id)
        bus.disconnect()
    return 0


def main(*, demo=False, style="dock"):
    return asyncio.run(run(demo=demo, style=style))
