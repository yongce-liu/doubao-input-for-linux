"""浮标矢量绘制；仅显示麦克风与状态，不显示转写文本。"""

from __future__ import annotations

import math

SIZE = 64


def rounded_rect(cr, x, y, width, height, radius):
    cr.new_sub_path()
    for cx, cy, start in (
        (x + width - radius, y + radius, -90),
        (x + width - radius, y + height - radius, 0),
        (x + radius, y + height - radius, 90),
        (x + radius, y + radius, 180),
    ):
        cr.arc(cx, cy, radius, math.radians(start), math.radians(start + 90))
    cr.close_path()


STATE_COLORS = {
    # 用户约定：录音=绿，处理=黄，错误/需登录=红，待机=灰。
    "idle": (0.55, 0.60, 0.68),
    "recording": (0.20, 0.80, 0.45),
    "processing": (1.0, 0.73, 0.25),
    "error": (0.98, 0.30, 0.36),
    "login-required": (0.98, 0.30, 0.36),
}

STATE_LABELS = {
    "idle": "豆包语音输入（待机）",
    "recording": "豆包语音输入：录音中…",
    "processing": "豆包语音输入：处理中…",
    "error": "豆包语音输入：发生错误，请查看服务日志",
    "login-required": "豆包语音输入：需要登录，请运行 doubao-input login",
}


def draw_badge(cr, state, width=SIZE, height=SIZE):
    color = STATE_COLORS.get(state, STATE_COLORS["idle"])
    cr.save()
    cr.scale(width / SIZE, height / SIZE)
    rounded_rect(cr, 2, 2, 60, 60, 18)
    cr.set_source_rgba(0.055, 0.075, 0.12, 0.96)
    cr.fill_preserve()
    cr.set_source_rgba(*color, 0.65)
    cr.set_line_width(1.5)
    cr.stroke()
    # 白色麦克风；右上角为状态灯。
    rounded_rect(cr, 26, 14, 12, 24, 6)
    cr.set_source_rgb(0.95, 0.97, 1.0)
    cr.fill()
    cr.set_line_width(2.8)
    cr.set_line_cap(1)
    cr.arc(32, 32, 12, 0, math.pi)
    cr.stroke()
    cr.move_to(32, 44)
    cr.line_to(32, 50)
    cr.move_to(26, 50)
    cr.line_to(38, 50)
    cr.stroke()
    cr.set_source_rgb(*color)
    cr.arc(49, 14, 4, 0, 2 * math.pi)
    cr.fill()
    if state == "processing":
        for x in (26, 32, 38):
            cr.arc(x, 56, 1.3, 0, 2 * math.pi)
            cr.fill()
    elif state in ("error", "login-required"):
        cr.set_line_width(3)
        cr.move_to(18, 47)
        cr.line_to(43, 20)
        cr.stroke()
    cr.restore()
