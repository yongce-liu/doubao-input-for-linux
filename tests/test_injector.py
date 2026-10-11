"""粘贴快捷键解析与 uinput 事件序列测试（不触碰真实设备）。"""

import os
import unittest
from unittest.mock import Mock, patch

from doubao_input import window_info
from doubao_input.inject import injector as module
from doubao_input.inject.injector import (
    KEY_INSERT,
    KEY_LEFTCTRL,
    KEY_LEFTSHIFT,
    KEY_V,
    Injector,
)


class FakeUInput:
    def __init__(self):
        self.events = []

    def write(self, ev_type, code, value):
        self.events.append((code, value))

    def syn(self):
        pass

    def close(self):
        pass


def keys(events):
    """折叠成 (按键, 值) 序列，忽略重复的 syn 分隔。"""
    return [(code, value) for code, value in events]


class ShortcutResolutionTests(unittest.TestCase):
    def test_unknown_shortcut_falls_back_to_ctrl_v(self):
        self.assertEqual(Injector().resolve_shortcut("nonsense"), "ctrl-v")

    def test_explicit_shortcuts_pass_through_untouched(self):
        for name in ("ctrl-v", "ctrl-shift-v", "shift-insert"):
            self.assertEqual(Injector().resolve_shortcut(name), name)

    def test_auto_picks_terminal_shortcut_for_terminals(self):
        with patch.object(
            window_info, "active_window_class", return_value="tabby"
        ):
            self.assertEqual(Injector().resolve_shortcut("auto"), "ctrl-shift-v")

    def test_auto_picks_shift_insert_for_xterm(self):
        with patch.object(
            window_info, "active_window_class", return_value="xterm"
        ):
            self.assertEqual(Injector().resolve_shortcut("auto"), "shift-insert")

    def test_auto_uses_ctrl_v_for_gui_apps(self):
        with patch.object(
            window_info, "active_window_class", return_value="firefox"
        ):
            self.assertEqual(Injector().resolve_shortcut("auto"), "ctrl-v")

    def test_auto_survives_detection_failure(self):
        with patch.object(
            window_info, "active_window_class", side_effect=OSError("no display")
        ):
            self.assertEqual(Injector().resolve_shortcut("auto"), "ctrl-v")


class KeySequenceTests(unittest.TestCase):
    """断言真实发出的按键序列与按下/释放顺序。"""

    def _run(self, shortcut):
        injector = Injector()
        fake = FakeUInput()
        with patch.object(injector, "_get_uinput", return_value=fake), patch.object(
            module.time, "sleep"
        ):
            self.assertTrue(injector._simulate_paste(shortcut))
        return keys(fake.events)

    def test_ctrl_v_sequence(self):
        self.assertEqual(
            self._run("ctrl-v"),
            [(KEY_LEFTCTRL, 1), (KEY_V, 1), (KEY_V, 0), (KEY_LEFTCTRL, 0)],
        )

    def test_ctrl_shift_v_sequence_presses_and_releases_in_order(self):
        self.assertEqual(
            self._run("ctrl-shift-v"),
            [
                (KEY_LEFTCTRL, 1),
                (KEY_LEFTSHIFT, 1),
                (KEY_V, 1),
                (KEY_V, 0),
                (KEY_LEFTSHIFT, 0),
                (KEY_LEFTCTRL, 0),
            ],
        )

    def test_shift_insert_sequence(self):
        self.assertEqual(
            self._run("shift-insert"),
            [
                (KEY_LEFTSHIFT, 1),
                (KEY_INSERT, 1),
                (KEY_INSERT, 0),
                (KEY_LEFTSHIFT, 0),
            ],
        )

    def test_uinput_declares_every_key_it_may_emit(self):
        """未在创建设备时声明的键码会被内核静默丢弃。"""
        injector = Injector()
        captured = {}

        def fake_uinput(**kwargs):
            captured.update(kwargs)
            return FakeUInput()

        fake_evdev = Mock()
        fake_evdev.UInput = fake_uinput
        fake_evdev.ecodes.EV_KEY = 1
        with patch.dict(
            "sys.modules", {"evdev": fake_evdev}
        ):
            injector._get_uinput()
        declared = captured["events"][1]
        for code in (KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_V, KEY_INSERT):
            self.assertIn(code, declared)


class ClipboardTests(unittest.TestCase):
    def test_clipboard_resolves_session_before_running_tools(self):
        injector = Injector()
        with patch.object(module.session_env, "ensure") as ensure, patch.object(
            module, "command_candidates", return_value=[]
        ):
            self.assertFalse(injector._copy_to_clipboard("hi"))
        ensure.assert_called_once()

    def test_empty_text_is_never_injected(self):
        self.assertFalse(Injector().inject(""))


if __name__ == "__main__":
    unittest.main()
