"""登录状态枚举；LoginWindow 用于回落检测。"""

from __future__ import annotations

from enum import Enum


class LoginStatus(Enum):
    CHECKING = "checking"
    LOGGED_IN = "logged_in"
    NOT_LOGGED_IN = "not_logged_in"


class AppState:
    """轻量占位对象，仅承载登录状态（历史接口兼容）。"""

    def __init__(self) -> None:
        self.login_status = LoginStatus.CHECKING
