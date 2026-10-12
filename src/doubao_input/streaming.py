"""单次识别连接：与 GTK 无关，连接建立前的音频也按序发送。"""

from __future__ import annotations

import asyncio
import json
import uuid
from urllib.parse import urlencode

from doubao_input.doubao.config import (
    AUTH_ERROR_CODE,
    AUTH_ERROR_KEYWORDS,
    FIXED_QUERY_PARAMS,
    ORIGIN,
    STOP_SAFETY_TIMEOUT,
    WSS_BASE_URL,
)


class AuthenticationError(RuntimeError):
    pass


async def recognize(
    params,
    audio: asyncio.Queue,
    on_open=lambda: None,
    *,
    connect=None,
    final_timeout=STOP_SAFETY_TIMEOUT,
) -> str:
    if connect is None:
        from websockets.asyncio.client import connect
    query = dict(
        FIXED_QUERY_PARAMS,
        device_id=params.device_id,
        web_id=params.web_id,
        tea_uuid=params.web_id,
        web_tab_id=str(uuid.uuid4()),
    )
    url = f"{WSS_BASE_URL}?{urlencode(query)}"
    headers = {"Cookie": params.cookie_header, "Origin": ORIGIN}
    text = ""
    # 不记录带身份信息的 URL、cookie 或识别内容。
    async with connect(
        url,
        open_timeout=5,
        close_timeout=1,
        max_size=2**20,
        additional_headers=headers,
    ) as ws:
        on_open()

        async def send():
            while True:
                chunk = await audio.get()
                if chunk is None:
                    return
                await ws.send(chunk)

        async def receive():
            nonlocal text
            async for message in ws:
                try:
                    data = json.loads(message)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(data, dict):
                    continue
                code = data.get("code", 0)
                message = str(data.get("message", "")).lower()
                if code != 0:
                    if code == AUTH_ERROR_CODE or any(
                        k in message for k in AUTH_ERROR_KEYWORDS
                    ):
                        raise AuthenticationError(
                            "登录已过期，请运行 doubao-input login"
                        )
                    raise RuntimeError(f"识别服务返回错误，code={code}")
                if data.get("event") == "result":
                    result = data.get("result") or {}
                    if (
                        isinstance(result, dict)
                        and isinstance(result.get("Text"), str)
                        and result["Text"]
                    ):
                        text = result["Text"]
                elif data.get("event") == "finish":
                    return
            raise RuntimeError("识别连接提前关闭，请重试")

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait(
                (sender, receiver), return_when=asyncio.FIRST_COMPLETED
            )
            if receiver in done:
                await receiver  # 传播认证、协议和连接异常。
            else:
                await sender
                # 协议沿用现有实现：停止发送 PCM，等待 finish 或有界超时。
                # 不虚构结束包；保留超时前最后一条识别结果。
                try:
                    await asyncio.wait_for(asyncio.shield(receiver), final_timeout)
                except TimeoutError:
                    pass
            return text.strip()
        finally:
            for task in (sender, receiver):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
