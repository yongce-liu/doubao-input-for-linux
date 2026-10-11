"""PyInstaller 冻结入口：让登录 / 浮标能使用系统 GTK / Cairo。"""
from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False):
    candidates = [
        f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages",
        "/usr/lib/python3/dist-packages",
    ]
    # 追加（而非插入）系统路径：捆绑的 Python 依赖优先，系统只补 gi / Gtk / cairo。
    for path in candidates:
        if os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)

from doubao_input.__main__ import main

raise SystemExit(main())
