# CLAUDE.md

Project guidance for working on **doubao-input** (豆包语音输入 Linux 轻量版).

## What this is

Press-and-hold voice input for Linux desktops (X11 优先，Wayland 降级到系统通知).
按住右 Ctrl 说话，松开后识别结果经剪贴板 + uinput 以 Ctrl+V 粘入当前焦点输入框。
无常驻窗口；状态由系统托盘图标反馈（待机灰/录音绿/处理黄/错误红）。

架构分三个进程，核心不加载任何 GUI 库：

1. **核心服务**（`serve`，asyncio 主循环）——evdev 热键、录音、ASR、粘贴。
2. **托盘图标子进程**（`_indicator`）——stdin 只收状态枚举，stdout 只回
   `event:*` 命令行；双向绝不传识别文本、音频或凭证。
3. **登录子进程**（`login`，WebKit 按需加载）——成功提取凭证后退出，
   服务下一次按键自动读取。

`doubao/` 协议层最初移植自上游 doubao-murmur（MIT，见 NOTICE）；轻量版已重构，
`streaming.py` 的 asyncio 协程是唯一识别路径。设计见 docs/lightweight.md。

## File map

```
src/doubao_input/
├── __main__.py            CLI：serve(默认)/login/status/devices/set-device/
│                          test-mic/doctor/install-service/_indicator/indicator-demo
├── service.py             VoiceService：asyncio 状态机 + 托盘事件处理。
│                          idle→connecting→recording→finishing→pasting→idle
├── streaming.py           recognize(): 队列 → ASR 的 asyncio 协程（有界队列 64 块）
├── feedback.py            FeedbackClient：拉托盘子进程，写状态 / poll_events 读事件
├── indicator.py           托盘进程：DockStatusIcon(StatusIcon)/X11Badge/通知；
│                          左键 toggle、右键菜单（mic/hotkey/login/quit）
├── indicator_art.py       cairo 矢量图标绘制
├── login_app.py           登录入口：有凭证先询问"保持登录/退出登录"
├── runtime.py             InstanceLock(flock) + status.json 原子写入
├── doubao/
│   ├── asr_client.py      WSS 协议（唯一 import websockets 的模块）
│   ├── audio_capture.py   sounddevice 16kHz/mono/int16
│   ├── config.py          常量、超时、XDG 路径（新常量放这里）
│   ├── login_window.py    WebKit 登录窗口 + JS 注入 + 凭证提取
│   ├── params_store.py    asr_params.json 读写（0600 + 原子写入）
│   ├── app_state.py       LoginStatus 枚举
│   └── host_tools.py      wl-copy / xclip 探测
├── trigger/evdev_ptt.py   后台线程 raw 读 /dev/input/event*；HOTKEYS 表 +
│                          load/save_hotkey；不 grab 键盘；1s 重扫支持热插拔
├── inject/injector.py     wl-copy/xclip + UInput 注入；事件间隔 GAP=0.012 不能删
├── window_info.py         读 _NET_ACTIVE_WINDOW 的 WM_CLASS，判断焦点窗口是不是终端
├── session_env.py         运行时解析 DISPLAY/XAUTHORITY（systemd 环境里没有）
└── login/resources/       WebView 注入 JS（profile 拦截 + localStorage 读取）

tests/                     unittest（离线，假音频/假 WSS）；probe/ 是手动联调脚本
packaging/                 binary_entry.py(PyInstaller 入口)、build-deb.sh、udev 规则
```

## Commands

```bash
# 开发与测试统一用 uv（AGENTS.md 要求）
uv run --no-sync python -m doubao_input serve
uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
./start.sh serve            # 等价快捷方式

# 手动联调（需要真实桌面与登录）
.venv/bin/python tests/probe/P5_login.py
.venv/bin/python tests/probe/P6_asr.py

# 打包单文件二进制（README 有完整命令）
uv run --no-sync pyinstaller --onefile --name doubao-input ... packaging/binary_entry.py
```

## Conventions

- UI 文本与注释用中文，标识符用英文；日志保持 INFO 短句、可 grep。
- 核心进程禁止 import gi/GTK/WebKit/Cairo；GUI 代码只能出现在 indicator.py 与
  login 相关模块。service.py 与托盘子进程之间只传固定状态/命令字符串。
- evdev/PortAudio 的线程回调只投递到 asyncio 循环（call_soon_threadsafe），
  不做业务逻辑。
- 凭证/识别文本不落日志、不落 status.json；网络异常的完整 URL 不打日志。
- 改协议前先看 asr_client.py 与 docs/lightweight.md 的"尚未声称解决的问题"。

## Things that have bitten before

- uinput 事件间隔 GAP=0.012：去掉后 GTK 把 Ctrl+V 合并成无操作。
- 终端不能收 Ctrl+V（readline 的 quoted-insert），必须 Ctrl+Shift+V 或
  Shift+Insert；粘贴快捷键默认 auto，按焦点的 WM_CLASS 判断。新增 uinput
  可发键码时必须同步加进 _get_uinput 的声明，否则内核静默丢弃。
- 物理右 Ctrl 未松开就粘贴会和注入的 Ctrl 竞争；PASTE_DELAY 不能删。
- evdev value=2 是自动重复，必须忽略，否则一次按键变多次。
- 音频缓冲上限 64 块、最长录音 120 秒、<150ms 视为误触——都是刻意边界。
- 冻结二进制缺的模块要显式 --hidden-import（如 optparse）；GTK 系必须
  --exclude-module，运行时靠 binary_entry.py 追加系统 dist-packages。
- 自启 unit 必须用 WantedBy=default.target：Cinnamon/XFCE 不会激活
  graphical-session.target，用它等于开机不自启。
- systemd 用户服务的环境里没有 DISPLAY，且进程环境是启动快照不会更新；
  任何要用 X 的路径（托盘图标、xclip）都必须先过 session_env.ensure()，
  不要直接读 os.environ 判断显示会话。图标失败由 _await_display 退避重试。
- runtime_dir() 缺 XDG_RUNTIME_DIR 时回落 /run/user/<uid>，否则实例锁
  会分裂，两个服务同时抢麦克风。
- ruff 会重排 import；用字符串替换打补丁前断言旧串存在。
