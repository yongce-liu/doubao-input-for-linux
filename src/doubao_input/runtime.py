"""每用户实例锁和状态；源码运行与 systemd 共用同一把锁。"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path


def runtime_dir() -> Path:
    # 会话外（ssh、sudo -i、cron、部分启动脚本）没有 XDG_RUNTIME_DIR。
    # 若各自回落到不同目录，实例锁与状态文件就会分裂，导致两个服务
    # 同时抢麦克风、status 也查不到真正的那个进程，因此统一回落到
    # logind 的标准会话目录 /run/user/<uid>。
    base = os.environ.get("XDG_RUNTIME_DIR")
    fallback = Path(f"/run/user/{os.getuid()}")
    if base and Path(base).is_dir():
        root = Path(base)
    elif fallback.is_dir():
        root = fallback
    else:
        root = Path.home() / ".cache"
    path = root / "doubao-input"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


class InstanceLock:
    def __enter__(self):
        self.file = (runtime_dir() / "service.lock").open("a+")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise RuntimeError(
                "已有豆包输入进程运行；请先停止旧进程或用户服务。"
            ) from None
        return self

    def __exit__(self, *exc):
        self.file.close()


def write_status(state: str, error: str | None = None) -> None:
    root = runtime_dir()
    # 不在状态文件中保存识别文本或登录凭证。
    data = {"pid": os.getpid(), "state": state, "error": error}
    fd, name = tempfile.mkstemp(dir=root)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False)
        os.replace(name, root / "status.json")
    finally:
        Path(name).unlink(missing_ok=True)


def read_status() -> dict:
    try:
        with InstanceLock():
            return {"state": "stopped"}
    except RuntimeError:
        try:
            return json.loads((runtime_dir() / "status.json").read_text())
        except (OSError, ValueError):
            return {"state": "running"}
