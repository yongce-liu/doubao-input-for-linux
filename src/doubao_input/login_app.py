"""通过独立 Chromium 会话登录，不加载系统 Python GUI 扩展。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path

from doubao_input.doubao.config import LOGIN_URL
from doubao_input.doubao.params_store import ASRParams, ParamsStore


def find_browser():
    override = os.environ.get("DOUBAO_BROWSER")
    for name in (
        [override]
        if override
        else [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "brave-browser",
        ]
    ):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError(
        "请安装 Chrome/Chromium，或设置 DOUBAO_BROWSER 为浏览器可执行文件路径"
    )


class CDP:
    def __init__(self, socket):
        self.socket = socket
        self.serial = 0

    async def call(self, method, params=None):
        self.serial += 1
        await self.socket.send(
            json.dumps(
                {
                    "id": self.serial,
                    "method": method,
                    "params": params or {},
                }
            )
        )
        async with asyncio.timeout(15):
            async for raw in self.socket:
                reply = json.loads(raw)
                if reply.get("id") == self.serial:
                    if "error" in reply:
                        raise RuntimeError("浏览器调试命令失败：" + method)
                    return reply.get("result", {})
        raise RuntimeError("登录浏览器已断开")


def extract_params(value, cookies):
    if not isinstance(value, dict) or not value.get("loggedIn"):
        return None
    try:
        device = json.loads(value.get("device") or "{}").get("web_id")
        web = json.loads(value.get("web") or "{}").get("web_id")
    except (ValueError, AttributeError, TypeError):
        return None
    jar = {
        c["name"]: c["value"]
        for c in cookies
        if c.get("domain", "").lstrip(".") == "doubao.com"
        or c.get("domain", "").endswith(".doubao.com")
    }
    if jar and isinstance(device, str) and device and isinstance(web, str) and web:
        return ASRParams(jar, device, web)
    return None


async def browser_login(browser, profile):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        # PyInstaller 的库搜索路径只适用于自身，不能传给系统浏览器。
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    process = subprocess.Popen(  # noqa: ASYNC220 -- 仅启动，所有 wait 在线程中执行
        [
            browser,
            f"--user-data-dir={profile}",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    port = endpoint = None
    try:
        active = Path(profile) / "DevToolsActivePort"
        for _ in range(300):
            if process.poll() is not None:
                raise RuntimeError(
                    "浏览器启动失败；请在桌面终端运行，并检查 DOUBAO_BROWSER"
                )
            try:
                port, endpoint = active.read_text().splitlines()[:2]
                if port.isdigit() and endpoint.startswith("/devtools/browser/"):
                    break
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError(
                "等待浏览器超时；Snap 浏览器可能无法访问独立配置目录，请使用原生 Chrome"
            )
        async with connect(
            f"ws://127.0.0.1:{port}{endpoint}", max_size=4 * 1024 * 1024
        ) as socket:
            browser_cdp = CDP(socket)
            targets = (await browser_cdp.call("Target.getTargets"))["targetInfos"]
            target = next(t["targetId"] for t in targets if t["type"] == "page")
            async with connect(
                f"ws://127.0.0.1:{port}/devtools/page/{target}",
                max_size=4 * 1024 * 1024,
            ) as page_socket:
                page = CDP(page_socket)
                await page.call("Page.enable")
                script = (
                    Path(__file__).parent / "login/resources/inject-websocket.js"
                ).read_text()
                bridge = "window.webkit={messageHandlers:{asrHandler:{postMessage:m=>{window.__doubaoLoggedIn=m.status==='loggedIn';}}}};"
                await page.call(
                    "Page.addScriptToEvaluateOnNewDocument", {"source": bridge + script}
                )
                await page.call("Page.navigate", {"url": LOGIN_URL})
                print(
                    "请在打开的独立浏览器窗口登录豆包；登录成功后自动关闭。", flush=True
                )
                async with asyncio.timeout(600):
                    while process.poll() is None:
                        reply = await page.call(
                            "Runtime.evaluate",
                            {
                                "expression": "location.hostname === 'www.doubao.com' || location.hostname === 'doubao.com' ? ({loggedIn:window.__doubaoLoggedIn===true,device:localStorage.getItem('samantha_web_web_id'),web:localStorage.getItem('__tea_cache_tokens_497858')}) : null",
                                "returnByValue": True,
                            },
                        )
                        cookies = await page.call(
                            "Network.getCookies", {"urls": [LOGIN_URL]}
                        )
                        params = extract_params(
                            reply.get("result", {}).get("value"),
                            cookies.get("cookies", []),
                        )
                        if params:
                            ParamsStore.save(params)
                            if ParamsStore.load() != params:
                                raise RuntimeError("登录凭证保存失败")
                            print("登录凭证已保存。后台服务在下一次按键时自动读取。")
                            return 0
                        await asyncio.sleep(1)
        return 1
    finally:
        if port and endpoint and process.poll() is None:
            # 浏览器关闭连接后可能来不及返回命令结果。
            with suppress(ConnectionClosed, OSError, TimeoutError, RuntimeError):
                async with asyncio.timeout(3):
                    async with connect(f"ws://127.0.0.1:{port}{endpoint}") as socket:
                        await CDP(socket).call("Browser.close")
        if process.poll() is None:
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)


def run_login(*, force=False):
    if ParamsStore.load() and not force:
        print("已保存登录凭证；如需重新登录，请运行 doubao-input login --force")
        return 0
    browser = find_browser()
    # 使用独立目录，不接触用户已有浏览器的历史、Cookie 或配置。
    with tempfile.TemporaryDirectory(prefix="doubao-login-") as profile:
        try:
            return asyncio.run(browser_login(browser, profile))
        except TimeoutError:
            raise RuntimeError("登录超时，请重新运行 doubao-input login") from None
