import asyncio
import json
import unittest
from urllib.parse import parse_qs, urlparse

from doubao_input.doubao.params_store import ASRParams
from doubao_input.streaming import AuthenticationError, recognize

PARAMS = ASRParams({"session": "fake"}, "device", "web")


class Socket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()
        self.closed = False

    async def send(self, chunk):
        self.sent.append(chunk)

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return json.dumps(message)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_url_and_headers_preserve_protocol_parameters(self):
        socket = Socket()
        queue = asyncio.Queue()
        queue.put_nowait(None)
        socket.incoming.put_nowait({"event": "finish"})

        def connect(url, **kwargs):
            query = parse_qs(urlparse(url).query)
            self.assertEqual(query["device_id"], [PARAMS.device_id])
            self.assertEqual(query["web_id"], [PARAMS.web_id])
            self.assertEqual(query["tea_uuid"], [PARAMS.web_id])
            self.assertEqual(query["format"], ["pcm"])
            self.assertTrue(query["web_tab_id"][0])
            self.assertEqual(
                kwargs["additional_headers"]["Cookie"], PARAMS.cookie_header
            )
            return socket

        await recognize(PARAMS, queue, connect=connect)

    async def test_default_timeout_keeps_final_sentence_arriving_after_one_second(self):
        socket = Socket()
        queue = asyncio.Queue()
        queue.put_nowait(None)

        async def results():
            socket.incoming.put_nowait(
                {"event": "result", "result": {"Text": "前半句"}}
            )
            await asyncio.sleep(1.1)
            socket.incoming.put_nowait(
                {"event": "result", "result": {"Text": "前半句和最后一句"}}
            )
            socket.incoming.put_nowait({"event": "finish"})

        producer = asyncio.create_task(results())
        try:
            result = await recognize(PARAMS, queue, connect=lambda *a, **k: socket)
            self.assertEqual(result, "前半句和最后一句")
        finally:
            await producer

    async def test_release_before_connection_preserves_all_audio(self):
        socket = Socket()
        queue = asyncio.Queue()
        for chunk in (b"first", b"second", None):
            queue.put_nowait(chunk)
        socket.incoming.put_nowait({"event": "result", "result": {"Text": "测试"}})
        result = await recognize(
            PARAMS, queue, connect=lambda *a, **k: socket, final_timeout=0.01
        )
        self.assertEqual(socket.sent, [b"first", b"second"])
        self.assertEqual(result, "测试")
        self.assertTrue(socket.closed)

    async def test_waits_for_final_update_instead_of_first_partial(self):
        socket = Socket()
        queue = asyncio.Queue()
        queue.put_nowait(None)

        async def results():
            socket.incoming.put_nowait({"event": "result", "result": {"Text": "半"}})
            await asyncio.sleep(0.01)
            socket.incoming.put_nowait(
                {"event": "result", "result": {"Text": "完整结果"}}
            )
            socket.incoming.put_nowait({"event": "finish"})

        producer = asyncio.create_task(results())
        result = await recognize(
            PARAMS, queue, connect=lambda *a, **k: socket, final_timeout=0.2
        )
        await producer
        self.assertEqual(result, "完整结果")

    async def test_auth_failure_propagates_and_closes_connection(self):
        socket = Socket()
        socket.incoming.put_nowait({"code": 709599054})
        with self.assertRaises(AuthenticationError):
            await recognize(PARAMS, asyncio.Queue(), connect=lambda *a, **k: socket)
        self.assertTrue(socket.closed)

    async def test_early_disconnect_does_not_paste_partial_result(self):
        socket = Socket()
        socket.incoming.put_nowait({"event": "result", "result": {"Text": "partial"}})
        socket.incoming.put_nowait(None)
        with self.assertRaises(RuntimeError):
            await recognize(PARAMS, asyncio.Queue(), connect=lambda *a, **k: socket)

    async def test_cancellation_closes_socket_and_children(self):
        socket = Socket()
        task = asyncio.create_task(
            recognize(PARAMS, asyncio.Queue(), connect=lambda *a, **k: socket)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(socket.closed)
        self.assertFalse(
            [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        )

    async def test_network_failure_before_connection(self):
        def connect(*a, **k):
            raise OSError("offline")

        with self.assertRaises(OSError):
            await recognize(PARAMS, asyncio.Queue(), connect=connect)
