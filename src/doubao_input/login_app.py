"""按需登录进程：登录成功退出，后台服务不加载 GTK/WebKit。

已保存凭证时先询问：继续使用现有登录，或退出登录后重新扫码；
未保存凭证时直接打开豆包网页登录窗口。
"""

from __future__ import annotations


def run_login() -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk

    from doubao_input.doubao.app_state import AppState
    from doubao_input.doubao.login_window import LoginWindow
    from doubao_input.doubao.params_store import ParamsStore

    if not LoginWindow.is_available():
        raise RuntimeError("请安装 python3-gi、gir1.2-gtk-4.0 和 gir1.2-webkit-6.0")
    if not Gtk.init_check():
        raise RuntimeError("无法连接桌面会话；请在桌面终端运行 login，勿使用 sudo")

    loop = GLib.MainLoop()
    result = 1  # 用户关闭窗口未登录视为失败
    holder = {"window": None, "timer": None, "pending": False}

    def finish(code):
        nonlocal result
        result = code
        loop.quit()

    def start_webview_login(window=None):
        """打开豆包网页登录；检测到已登录态后提取凭证并退出。"""
        if window is None:
            window = LoginWindow(AppState())
        holder["window"] = window

        def extracted(params):
            holder["pending"] = False
            if params:
                ParamsStore.save(params)
                if ParamsStore.load() == params:
                    print("登录凭证已保存。后台服务在下一次按键时自动读取。")
                    finish(0)

        def extract():
            holder["timer"] = None
            window.extract_params_async(extracted)
            return False

        def on_login(status, nickname):
            if status == "loggedIn" and not holder["pending"]:
                holder["pending"] = True
                holder["timer"] = GLib.timeout_add(800, extract)

        window.on_close = loop.quit
        window._on_login_status_change = on_login
        window.load()
        window.show()

    def show_status_window():
        """本地已有登录凭证：询问继续使用还是退出登录。"""
        dialog = Gtk.Window(title="豆包语音输入 - 登录状态")
        dialog.set_default_size(420, 180)
        dialog.set_resizable(False)
        holder["window"] = dialog

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(24)
        label = Gtk.Label(
            label="检测到已保存的豆包登录信息。\n"
            "可直接关闭本窗口继续使用，或退出登录后重新登录。"
        )
        label.set_wrap(True)
        box.append(label)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons.set_halign(Gtk.Align.CENTER)
        keep = Gtk.Button(label="保持登录并关闭")
        logout = Gtk.Button(label="退出登录")
        logout.add_css_class("destructive-action")

        keep.connect("clicked", lambda *_: finish(0))

        def on_logout(*_args):
            ParamsStore.clear()
            dialog.set_visible(False)
            # 清空网页侧会话数据后再进入重新登录，避免旧会话自动登录。
            window = LoginWindow(AppState())
            window._setup()
            window._clear_website_data(lambda: start_webview_login(window))

        logout.connect("clicked", on_logout)
        buttons.append(keep)
        buttons.append(logout)
        box.append(buttons)
        dialog.set_child(box)
        dialog.connect("close-request", lambda *_: (finish(0), True)[1])
        dialog.present()

    if ParamsStore.has_saved():
        show_status_window()
    else:
        start_webview_login()
    try:
        loop.run()
    finally:
        if holder["timer"] is not None:
            GLib.source_remove(holder["timer"])
        window = holder["window"]
        if isinstance(window, LoginWindow):
            window.destroy()
        elif window is not None:
            window.destroy()
    return result
