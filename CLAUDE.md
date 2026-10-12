# CLAUDE.md

Project guidance for working on **doubao-input** (豆包语音输入 Linux 轻量版).

## What this is

Press-and-hold voice input for Linux desktops (D-Bus 托盘，无宿主则使用通知).
按住右 Ctrl 说话，松开后识别结果经剪贴板 + uinput 以 Ctrl+V 粘入当前焦点输入框。
无常驻窗口；状态由系统托盘图标反馈（待机灰/录音绿/处理黄/错误红）。

架构分三个进程，核心不加载任何 GUI 库：

1. **核心服务**（`serve`，asyncio 主循环）——evdev 热键、录音、ASR、粘贴。
2. **托盘图标子进程**（`_indicator`）——stdin 只收状态枚举，stdout 只回
   `event:*` 命令行；双向绝不传识别文本、音频或凭证。
3. **登录子进程**（`login`，系统 Chrome/Chromium CDP）——成功提取凭证后退出，
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
├── dbus_indicator.py      StatusNotifierItem/DBusMenu/通知（dbus-next asyncio）
├── login_app.py           浏览器独立临时会话登录；--force 重新登录
├── runtime.py             InstanceLock(flock) + status.json 原子写入
├── doubao/
│   ├── audio_capture.py   sounddevice 16kHz/mono/int16
│   ├── config.py          常量、超时、XDG 路径（新常量放这里）
│   ├── params_store.py    asr_params.json 读写（0600 + 原子写入）
│   └── host_tools.py      wl-copy / xclip 探测
├── trigger/evdev_ptt.py   后台线程 raw 读 /dev/input/event*；HOTKEYS 表 +
│                          load/save_hotkey；不 grab 键盘；1s 重扫支持热插拔
├── inject/injector.py     wl-copy/xclip + UInput 注入；事件间隔 GAP=0.012 不能删
├── window_info.py         读 _NET_ACTIVE_WINDOW 的 WM_CLASS，判断焦点窗口是不是终端
├── session_env.py         运行时解析 DISPLAY/XAUTHORITY（systemd 环境里没有）
└── login/resources/       浏览器注入 JS（profile 拦截）

tests/                     unittest（假音频/假 WSS、真实独立 D-Bus 总线）
packaging/                 binary_entry.py(PyInstaller 入口)、build-binary.sh、
                           build-deb.sh、udev 规则
```

## Commands

```bash
# 开发与测试用 uv
uv run --no-sync python -m doubao_input serve
uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
./start.sh serve            # 等价快捷方式

# 手动联调（需要真实桌面与登录）
uv run --no-sync python -m doubao_input login
uv run --no-sync python -m doubao_input test-mic

# 打包单文件二进制（README 有完整命令）
bash packaging/build-binary.sh
bash packaging/build-deb.sh  # Docker 内构建独立运行的 deb
```

## Conventions

- UI 文本与注释用中文，标识符用英文；日志保持 INFO 短句、可 grep。
- 核心进程禁止 import gi/GTK/WebKit/Cairo；界面代码只能出现在 dbus_indicator.py 与
  login 相关模块。service.py 与托盘子进程之间只传固定状态/命令字符串。
- evdev/PortAudio 的线程回调只投递到 asyncio 循环（call_soon_threadsafe），
  不做业务逻辑。
- 凭证/识别文本不落日志、不落 status.json；网络异常的完整 URL 不打日志。
- 改协议前先看 streaming.py 与 docs/lightweight.md 的限制说明。

## Things that have bitten before

- uinput 事件间隔 GAP=0.012：去掉后 GTK 把 Ctrl+V 合并成无操作。
- 终端不能收 Ctrl+V（readline 的 quoted-insert），必须 Ctrl+Shift+V 或
  Shift+Insert；粘贴快捷键默认 auto，按焦点的 WM_CLASS 判断。新增 uinput
  可发键码时必须同步加进 _get_uinput 的声明，否则内核静默丢弃。
- 物理右 Ctrl 未松开就粘贴会和注入的 Ctrl 竞争；PASTE_DELAY 不能删。
- evdev value=2 是自动重复，必须忽略，否则一次按键变多次。
- 音频缓冲上限 64 块、最长录音 120 秒、<150ms 视为误触——都是刻意边界。
- 松开后的 400ms 尾音必须继续入队，stop() 后才能放本地结束标记；
  取消/超时/退出要清理尾音定时器，防止旧会话停止下一次录音。
- 停止发送 PCM 后最多等待 2 秒最终结果，不虚构服务端结束包。
- 冻结入口不再追加系统 dist-packages；运行依赖随包分发，gi/cairo 排除。
- packaging/hooks/hook-sounddevice.py 禁止收集系统 PortAudio/ALSA/JACK；
  打包旧音频库会遮蔽目标系统插件，丢失 pipewire/pulse 设备并报 Invalid sample rate。
- Ubuntu 22.04+ 发布包在 packaging/Dockerfile.binary 的 22.04 基线上构建，
  使用独立 .venv-build 与 Python 3.12；不与系统 Python ABI 绑定。
- deb 安装预构建二进制，不要对 PyInstaller 产物执行 strip/dwz；
  服务用 debian/doubao-input.user.service，仅按用户选择启用，不安装系统级录音服务。
- 登录浏览器必须用独立临时配置，冻结进程启动浏览器前恢复 LD_LIBRARY_PATH。
- D-Bus 方法注解是协议签名，dbus_indicator.py 不使用 postponed annotations。
- --indicator-style float 现在使用系统通知，不创建 GTK 浮标。
- 自启 unit 必须用 WantedBy=default.target：Cinnamon/XFCE 不会激活
  graphical-session.target，用它等于开机不自启。
- systemd 用户服务的环境里没有 DISPLAY，且进程环境是启动快照不会更新；
  任何要用 X 的路径（托盘图标、xclip）都必须先过 session_env.ensure()，
  不要直接读 os.environ 判断显示会话。图标失败由 _await_display 退避重试。
- runtime_dir() 缺 XDG_RUNTIME_DIR 时回落 /run/user/<uid>，否则实例锁
  会分裂，两个服务同时抢麦克风。
- ruff 会重排 import；用字符串替换打补丁前断言旧串存在。
