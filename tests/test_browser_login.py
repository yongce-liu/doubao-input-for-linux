import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from doubao_input.login_app import CDP, extract_params


class LoginParamsTests(unittest.TestCase):
    def test_requires_profile_success_and_filters_cookie_domains(self):
        value = {
            "loggedIn": True,
            "device": '{"web_id":"device"}',
            "web": '{"web_id":"web"}',
        }
        cookies = [
            {"domain": domain, "name": name, "value": "test"}
            for domain, name in [
                (".doubao.com", "session"),
                ("www.doubao.com", "host"),
                ("evildoubao.com", "bad"),
                ("doubao.com.evil.test", "bad2"),
            ]
        ]
        result = extract_params(value, cookies)
        self.assertEqual(result.cookies, {"session": "test", "host": "test"})
        self.assertEqual((result.device_id, result.web_id), ("device", "web"))
        self.assertIsNone(extract_params(dict(value, loggedIn=False), cookies))
        self.assertIsNone(extract_params(dict(value, device="invalid"), cookies))
        self.assertIsNone(extract_params(value, []))


class CDPTests(unittest.IsolatedAsyncioTestCase):
    async def test_ignores_events_and_matches_command_reply(self):
        class Socket:
            send = AsyncMock()

            async def __aiter__(self):
                for message in [
                    {"method": "Page.loadEventFired"},
                    {"id": 99},
                    {"id": 1, "result": {"value": "ok"}},
                ]:
                    yield json.dumps(message)

        socket = Socket()
        self.assertEqual(await CDP(socket).call("Test"), {"value": "ok"})
        self.assertEqual(json.loads(socket.send.call_args.args[0])["method"], "Test")

    async def test_real_websocket_round_trip(self):
        from websockets.asyncio.client import connect
        from websockets.asyncio.server import serve

        async def handler(socket):
            command = json.loads(await socket.recv())
            await socket.send(json.dumps({"method": "Page.frameNavigated"}))
            await socket.send(json.dumps({"id": command["id"], "result": {"ok": True}}))

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as socket:
                self.assertEqual(
                    await asyncio.wait_for(CDP(socket).call("Test"), 2), {"ok": True}
                )
