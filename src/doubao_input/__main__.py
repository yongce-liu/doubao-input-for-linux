"""轻量 CLI：语音核心、D-Bus 托盘、浏览器登录分进程。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from doubao_input.inject.injector import PASTE_SHORTCUTS


def parser():
    result = argparse.ArgumentParser(
        description="豆包语音输入：按住右 Ctrl 说话，松开粘贴；仅显示麦克风状态浮标"
    )
    result.add_argument("--debug", action="store_true")
    commands = result.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="运行后台核心（默认命令）")
    serve.add_argument(
        "--paste-shortcut",
        choices=list(PASTE_SHORTCUTS),
        default="auto",
        help="auto=按焦点窗口自动选择（默认）；终端会自动改用 Ctrl+Shift+V",
    )
    serve.add_argument("--no-indicator", action="store_true", help="禁用麦克风状态图标")
    serve.add_argument(
        "--indicator-style",
        choices=["dock", "float"],
        default="dock",
        help="dock=托盘常驻图标（默认）；float=系统通知",
    )
    commands.add_parser("indicator-demo", help="预览麦克风浮标，不录音、不连接豆包")
    login = commands.add_parser("login", help="使用系统浏览器登录，登录后退出")
    login.add_argument("--force", action="store_true", help="重新登录并替换已保存凭证")
    commands.add_parser("status", help="查看后台运行状态")
    commands.add_parser("devices", help="列出输入设备")
    device = commands.add_parser("set-device", help="保存麦克风选择，下次录音生效")
    device.add_argument("device", help="设备编号或 auto")
    mic = commands.add_parser("test-mic", help="录音测试，只输出音量，不上传音频")
    mic.add_argument("--seconds", type=float, default=3)
    commands.add_parser("doctor", help="检查权限、音频和会话环境")
    commands.add_parser(
        "install-service", help="生成当前环境的 systemd 用户服务（不自动启动）"
    )
    internal = commands.add_parser("_indicator", help=argparse.SUPPRESS)
    internal.add_argument(
        "--style", choices=["dock", "float"], default="dock"
    )  # 冻结二进制的内部浮标入口
    return result


def test_mic(seconds):
    from doubao_input.doubao.audio_capture import AudioCapture
    from doubao_input.runtime import InstanceLock
    from doubao_input.service import selected_device

    if not 0 < seconds <= 60:
        raise ValueError("--seconds 必须大于 0 且不超过 60")
    with InstanceLock():
        cap = AudioCapture(device=selected_device())
        levels = []
        cap._on_rms = levels.append
        print(f"请说话，检测 {seconds:g} 秒（音频不上传、不保存）…", flush=True)
        try:
            cap.start(lambda data: None)
            time.sleep(seconds)
        finally:
            cap.stop()
        if not levels:
            print("没有收到音频回调，请检查输入设备。")
            return 1
        print(
            f"blocks={len(levels)} peak={max(levels):.6f} avg={sum(levels) / len(levels):.6f}"
        )
        if max(levels) == 0:
            print("收到音频但音量为零，请检查静音和输入设备。")
            return 1
    return 0


def doctor():
    import evdev

    from doubao_input.doubao.audio_capture import AudioCapture
    from doubao_input.doubao.params_store import ParamsStore
    from doubao_input.runtime import read_status
    from doubao_input.service import selected_device
    from doubao_input import session_env

    # 与后台服务同样主动解析：从终端运行时通常已有 DISPLAY，此处仅补空值。
    session_env.ensure()
    checks = {
        "桌面会话": session_env.has_display(),
        "uinput 读写权限": os.access("/dev/uinput", os.R_OK | os.W_OK),
        "剪贴板工具": bool(
            shutil.which("wl-copy")
            if os.environ.get("WAYLAND_DISPLAY")
            else shutil.which("xclip")
        ),
        "本地登录凭证（未验证是否过期）": ParamsStore.load() is not None,
    }
    keyboards = []
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        try:
            if evdev.ecodes.KEY_RIGHTCTRL in device.capabilities().get(
                evdev.ecodes.EV_KEY, []
            ):
                keyboards.append(path)
        finally:
            device.close()
    checks["可读取的右 Ctrl 键盘"] = bool(keyboards)
    try:
        devices = AudioCapture.list_input_devices()
        checks["存在音频输入设备"] = bool(devices)
    except Exception as exc:  # noqa: BLE001 -- CLI 边界把依赖/系统错误转成非零退出码
        checks["存在音频输入设备"] = False
        print(f"音频枚举失败: {exc}")
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'FAIL'}: {name}")
    print("选定设备:", selected_device())
    print("后台状态:", json.dumps(read_status(), ensure_ascii=False))
    try:
        from doubao_input.inject.injector import Injector
        from doubao_input.window_info import active_window_class

        focused = active_window_class()
        print("当前焦点窗口:", focused or "无法判断（Wayland 或没有焦点窗口）")
        print("将使用的粘贴快捷键:", Injector().resolve_shortcut("auto"))
    except Exception as exc:  # noqa: BLE001 -- 诊断命令不应因探测失败而崩溃
        print("粘贴快捷键探测失败:", exc)
    return 0 if all(checks.values()) else 1


def install_service():
    # 此命令明确由用户触发；不自动启用、不要求 root。
    unit = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "systemd/user/doubao-input.service"
    )
    unit.parent.mkdir(parents=True, exist_ok=True)
    executable = (
        sys.executable.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
        .replace("$", "$$")
    )
    if "\n" in executable or "\r" in executable:
        raise ValueError("Python 路径不能含换行符")
    flags = "" if getattr(sys, "frozen", False) else "-m doubao_input "
    # 用 default.target 而非 graphical-session.target：Cinnamon/XFCE 等桌面
    # 不激活后者，会导致开机不自启。服务自身会在桌面会话就绪后补挂状态图标。
    content = f'''[Unit]
Description=Doubao lightweight voice input
After=graphical-session.target

[Service]
Type=simple
ExecStart="{executable}" {flags}serve
Restart=on-failure
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
'''
    if unit.exists():
        raise RuntimeError(f"服务文件已存在，未覆盖：{unit}；请先检查并备份旧配置")
    unit.write_text(content)
    print(f"已写入 {unit}，尚未启动。请在桌面终端执行：")
    print("systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY")
    print("systemctl --user daemon-reload")
    print("systemctl --user enable --now doubao-input.service")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    command = args.command or "serve"
    try:
        if command == "serve":
            from doubao_input.service import run_service

            return run_service(
                paste_shortcut=getattr(args, "paste_shortcut", "auto"),
                indicator=not getattr(args, "no_indicator", False),
                indicator_style=getattr(args, "indicator_style", "dock"),
            )
        if command == "_indicator":
            from doubao_input.dbus_indicator import main as run_indicator

            return run_indicator(style=args.style)
        if command == "indicator-demo":
            from doubao_input.dbus_indicator import main as run_indicator

            return run_indicator(demo=True)
        if command == "login":
            from doubao_input.login_app import run_login

            return run_login(force=args.force)
        if command == "status":
            from doubao_input.runtime import read_status

            print(json.dumps(read_status(), ensure_ascii=False, indent=2))
        elif command == "devices":
            from doubao_input.doubao.audio_capture import AudioCapture

            for device in AudioCapture.list_input_devices():
                print(
                    f"{device['index']}: {device['name']} ({device['max_input_channels']} ch)"
                )
        elif command == "set-device":
            from doubao_input.doubao.audio_capture import AudioCapture
            from doubao_input.doubao.config import get_audio_config_path

            value = None if args.device == "auto" else int(args.device)
            if value is not None and value not in {
                d["index"] for d in AudioCapture.list_input_devices()
            }:
                raise ValueError("无效的输入设备编号，请先运行 devices")
            get_audio_config_path().write_text(json.dumps({"device": value}))
            print("设备已保存，下次录音生效。")
        elif command == "test-mic":
            return test_mic(args.seconds)
        elif command == "doctor":
            return doctor()
        elif command == "install-service":
            install_service()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 -- CLI 边界把依赖/系统错误转成非零退出码
        logging.getLogger(__name__).error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
