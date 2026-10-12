# 豆包语音输入（Linux 轻量版）

按住 **右 Ctrl** 说话，松开后将识别结果粘贴到当前输入框。无常驻窗口，
通过系统托盘显示待机、录音、处理和错误状态。

- 松开 Ctrl 或点击停止后继续录音 **400 ms**，保留尾音；最终结果最多再等 **2 秒**。
- 托盘左键开始/停止；右键选择麦克风、快捷键、重新登录或退出。
- 自动识别终端窗口，使用 Ctrl+Shift+V（xterm 用 Shift+Insert），其它应用用 Ctrl+V。
- 登录使用系统 Chrome/Chromium，托盘使用 D-Bus，不依赖系统 Python/gi。

> 使用豆包网页登录凭证及非官方网页接口，音频会发往豆包服务器。
> `test-mic` 仅检查本地音量，不上传、不保存音频。

## 安装 deb（推荐）

目标为 Ubuntu 22.04 及以上、与安装包相同的 CPU 架构。

```bash
sudo apt install ./doubao-input_*.deb
# 注销桌面并重新登录，使安装时配置的 input 组权限生效
/usr/bin/doubao-input login
systemctl --user daemon-reload
systemctl --user enable --now doubao-input.service
```

deb 包含 Python 和程序依赖，apt 自动安装系统音频、剪贴板组件，
无需 uv、pip、虚拟环境或手动生成服务。安装时通过 `SUDO_USER` 为当前用户
配置 input 组；若使用图形安装器而无法确定用户，需执行
`sudo usermod -aG input "$USER"` 后注销再登录。

登录需要已安装 **原生 Chrome/Chromium**；推荐 Google Chrome。
Snap/Flatpak 浏览器的文件隔离可能阻止启动独立登录会话。
可用 `DOUBAO_BROWSER=/path/to/browser doubao-input login` 指定。
登录成功后删除临时浏览器配置，仅保留应用自己的凭证文件。

Ubuntu GNOME 需要启用 AppIndicator 扩展才能显示托盘；没有托盘宿主时使用系统通知。
Wayland 下通知可能受勿扰模式影响。

### 从旧版手动安装迁移

`~/.local/bin` 中的旧程序、`~/.config/systemd/user/doubao-input.service`
会覆盖 deb 版本。先停止旧服务，备份并移走旧用户 unit，再执行
`systemctl --user daemon-reload` 和上面的自启命令。
deb 的程序路径固定为 `/usr/bin/doubao-input`，凭证和设备配置继续沿用。

## 常用命令

| 命令 | 作用 |
|---|---|
| `login` / `login --force` | 首次登录 / 重新登录 |
| `serve` | 前台运行语音输入 |
| `serve --no-indicator` | 无界面运行 |
| `serve --indicator-style float` | 使用系统通知（保留旧选项名） |
| `serve --paste-shortcut ctrl-v` | 强制粘贴快捷键，默认 `auto` |
| `status` / `doctor` | 后台状态 / 环境自检 |
| `devices` / `set-device auto` | 列出设备 / 恢复自动选择，也可指定编号 |
| `test-mic --seconds 3` | 本地录音测试，需先停止后台服务 |
| `indicator-demo` | 预览状态图标，不录音、不连接豆包 |
| `install-service` | 仅源码/单文件安装需要，生成当前路径的用户服务 |

```bash
journalctl --user -u doubao-input.service -f
systemctl --user stop doubao-input.service
```

配置在 `~/.config/doubao-input/`（支持 XDG）：`asr_params.json` 保存凭证，
`audio.json` 保存麦克风，`trigger.json` 保存快捷键。
纯 Wayland 无法查询全局焦点窗口，自动粘贴回退到 Ctrl+V，可手动指定快捷键。

## 编译

发布包通过 Docker 在 Ubuntu 22.04 基线上构建，避免新 glibc 依赖。

```bash
bash packaging/build-deb.sh     # dist/doubao-input_<版本>_<架构>.deb
# 或单文件产物：
docker build -f packaging/Dockerfile.binary --output type=local,dest=dist/ubuntu22.04 .
```

本机构建单文件需要 uv、编译器和系统音频库：
```bash
sudo apt install build-essential libportaudio2
bash packaging/build-binary.sh  # dist/doubao-input，仅承诺当前构建系统基线
```

PortAudio/ALSA/JACK **使用目标系统的库**，避免旧库遮蔽系统 PipeWire/PulseAudio 插件。
单文件或源码安装需自行安装 `libportaudio2 libasound2-plugins wl-clipboard xclip x11-utils`，
配置设备权限（deb 已代办）：
```bash
sudo groupadd -f input
sudo usermod -aG input "$USER"
sudo install -m644 packaging/99-doubao-input-uinput.rules /etc/udev/rules.d/
sudo install -m644 packaging/doubao-input.conf /etc/modules-load.d/
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput
# 注销再登录；把二进制放到稳定路径后运行 doubao-input install-service
```

## 开发与测试

```bash
uv sync --locked --python 3.12
uv run --no-sync python -m doubao_input serve
uv run --no-sync python -m unittest discover -s tests -p 'test_*.py'
```

核心代码：`service.py` 管理会话，`streaming.py` 负责 ASR，
`doubao/audio_capture.py` 采集音频，`inject/injector.py` 粘贴，
`dbus_indicator.py` 提供托盘，`login_app.py` 处理浏览器登录。
架构边界及真实桌面验收见 [docs/lightweight.md](docs/lightweight.md)。
