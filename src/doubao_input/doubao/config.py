"""Constants and configuration for Doubao Murmur Linux port.

Mirrors the fixed parameters from the macOS DoubaoASRClient.swift.
"""

import os
from pathlib import Path

# --- Doubao ASR WebSocket ---

WSS_BASE_URL = "wss://ws-samantha.doubao.com/samantha/audio/asr"

FIXED_QUERY_PARAMS = {
    "version_code": "20800",
    "language": "zh",
    "device_platform": "web",
    "aid": "497858",
    "real_aid": "497858",
    "pkg_type": "release_version",
    "pc_version": "3.12.3",
    "region": "",
    "sys_region": "",
    "samantha_web": "1",
    "use-olympus-account": "1",
    "format": "pcm",
}

ORIGIN = "https://www.doubao.com"
LOGIN_URL = "https://www.doubao.com/chat"

# --- Audio capture ---

AUDIO_SAMPLE_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_BLOCKSIZE = 4096  # samples per callback (~256ms at 16kHz)

# --- Auth error detection ---

AUTH_ERROR_CODE = 709599054
AUTH_ERROR_KEYWORDS = [
    "cookie",
    "auth",
    "login",
    "session",
    "unauthorized",
    "expired",
]

# --- Paths ---

CONFIG_DIR_NAME = "doubao-input"
PARAMS_FILE = "asr_params.json"
AUDIO_FILE = "audio.json"
TRIGGER_FILE = "trigger.json"


def get_config_dir() -> Path:
    """Get the XDG config directory for the app."""
    config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    app_dir = Path(config_home) / CONFIG_DIR_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_params_path() -> Path:
    """Get the path to the ASR params JSON file."""
    return get_config_dir() / PARAMS_FILE


def get_audio_config_path() -> Path:
    return get_config_dir() / AUDIO_FILE


def get_trigger_config_path() -> Path:
    return get_config_dir() / TRIGGER_FILE


# --- Timeouts ---

RELEASE_TAIL_SECONDS = 0.4  # 松开后继续采集；大于一块音频，保留句末尾音。
STOP_SAFETY_TIMEOUT = 2.0  # 停止发送后等待服务端最终结果的上限。
PASTE_DELAY = 0.05  # 等待物理 Ctrl 释放，避免和虚拟粘贴快捷键叠加。
