import os
import shutil
import subprocess
import sys
import unittest


class DBusIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("dbus-run-session"), "需要 dbus-run-session")
    def test_export_menu_icons_and_receive_actions(self):
        code = """
import asyncio
from unittest.mock import patch
from dbus_next import Variant
from dbus_next.aio import MessageBus
from doubao_input.dbus_indicator import Menu, Tray

async def run():
    server = await MessageBus().connect()
    client = await MessageBus().connect()
    events = []
    with patch('doubao_input.doubao.audio_capture.AudioCapture.list_input_devices', return_value=[]):
        menu = Menu(events.append)
    tray = Tray(events.append)
    server.export('/MenuBar', menu)
    server.export('/StatusNotifierItem', tray)
    try:
        intro = await client.introspect(server.unique_name, '/MenuBar')
        proxy = client.get_proxy_object(server.unique_name, '/MenuBar', intro).get_interface('com.canonical.dbusmenu')
        revision, root = await proxy.call_get_layout(0, -1, [])
        assert root[0] == 0 and len(root[2]) == 5
        toggle = root[2][0].value[0]
        await proxy.call_event(toggle, 'clicked', Variant('i', 0), 0)
        assert events == ['toggle']
        props = await proxy.call_get_group_properties([toggle], ['label'])
        assert list(props[0][1]) == ['label']
        with patch('doubao_input.doubao.audio_capture.AudioCapture.list_input_devices', return_value=[]):
            assert await proxy.call_about_to_show_group([0]) == [[0], []]
        intro = await client.introspect(server.unique_name, '/StatusNotifierItem')
        proxy = client.get_proxy_object(server.unique_name, '/StatusNotifierItem', intro).get_interface('org.kde.StatusNotifierItem')
        assert (await proxy.get_icon_pixmap())[0][:2] == [32, 32]
        tray.update('recording')
        assert await proxy.get_title() == '正在录音'
        await proxy.call_activate(0, 0)
        assert events == ['toggle', 'toggle']
    finally:
        client.disconnect()
        server.disconnect()
        await client.wait_for_disconnect()
        await server.wait_for_disconnect()

asyncio.run(run())
"""
        result = subprocess.run(
            ["dbus-run-session", "--", sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=dict(os.environ),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
