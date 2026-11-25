"""Translation tests use native layout APIs, never physical/injected key presses."""
import ctypes
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(sys.platform == "win32", "Windows keyboard translation")
class TextInputTests(unittest.TestCase):
    def setUp(self):
        from geminisecretai import hotkeys as h, client as c
        self.h, self.c = h, c
        self.composer = h.DeadKeyComposer()
        h.text_composer.reset()
        self.addCleanup(h.text_composer.reset)

    def type_text(self, text):
        result = ""
        for char in text:
            if char == "^":
                result += self.composer.feed("^", dead=True)
                result += self.composer.feed(" ")
            else:
                result += self.composer.feed(char)
        return result

    def test_literal_carets_and_formulas(self):
        for text in ("2^3", "x^2 + y^2", "SO4^2-", "^^^ a^b ^"):
            self.assertEqual(self.type_text(text), text)

    def test_accent_composition_repetition_and_fallback(self):
        for accent, base, expected in (("^", "e", "ê"), ("´", "e", "é"), ("¨", "u", "ü"),
                                       ("~", "n", "ñ"), ("^", "2", "^2")):
            self.assertEqual(self.composer.feed(accent, True), "")
            self.assertEqual(self.composer.feed(base), expected)
        self.composer.feed("^", True)
        self.assertEqual(self.composer.feed("^", True), "^")
        self.assertEqual(self.composer.feed("a"), "a")

    def test_backspace_reset_and_layout_change_clear_pending_accent(self):
        self.composer.feed("^", True)
        self.assertTrue(self.composer.backspace())
        self.assertEqual(self.composer.feed("e"), "e")
        self.composer.feed("^", True)
        self.composer.reset()
        self.assertEqual(self.composer.feed("e"), "e")
        self.composer.feed("^", True, layout=1)
        self.assertEqual(self.composer.feed("e", layout=2), "e")

    def test_native_danish_layout_dead_key_and_altgr(self):
        h = self.h
        count = h.user32.GetKeyboardLayoutList(0, None)
        existing = (ctypes.c_void_p * count)()
        h.user32.GetKeyboardLayoutList(count, existing)
        h.user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
        layout = h.user32.LoadKeyboardLayoutW("00000406", 0)  # Load, do not activate.
        self.assertTrue(layout)
        if layout not in existing:
            self.addCleanup(lambda: h.user32.UnloadKeyboardLayout(ctypes.c_void_p(layout)))
        found = False
        for vk in range(0xBA, 0xE3):
            for modifiers in (set(), {0x10}, {0x11, 0x12, 0xA5}):
                status = (ctypes.c_ubyte * 256)()
                for key in modifiers | {vk}:
                    status[key] = 0x80
                out = ctypes.create_unicode_buffer(8)
                length = h.user32.ToUnicodeEx(vk, 0, status, out, 8, 4, layout)
                if length < 0 and out.value == "^":
                    with patch.object(h.user32, "GetKeyboardLayout", return_value=layout), \
                         patch.object(h.user32, "GetKeyState", return_value=0), \
                         patch.object(h, "down", side_effect=lambda key: key in modifiers):
                        self.assertEqual(h.translate_key(vk, 0), "")
                    with patch.object(h.user32, "GetKeyboardLayout", return_value=layout), \
                         patch.object(h.user32, "GetKeyState", return_value=0), \
                         patch.object(h, "down", return_value=False):
                        self.assertEqual(h.translate_key(0x20, 0), "^")
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "Danish layout must expose a circumflex dead key")
        with patch.object(h.user32, "GetKeyboardLayout", return_value=layout), \
             patch.object(h.user32, "GetKeyState", return_value=0), \
             patch.object(h, "down", side_effect=lambda key: key == 0xA5):
            self.assertEqual(h.translate_key(ord("2"), 0), "@")

    def test_multi_character_translation_backspace_and_cancel(self):
        c = self.c
        saved_state, saved_buffer = c.state, list(c.buffer)
        self.addCleanup(lambda: setattr(c, "state", saved_state))
        self.addCleanup(lambda: c.buffer.__setitem__(slice(None), saved_buffer))
        c.state = c.RECORDING
        c.buffer[:] = ["a"]
        with patch.object(c, "down", return_value=False), patch.object(c, "translate_key", return_value="^2"):
            c.process(0x100, SimpleNamespace(flags=0, vkCode=ord("2"), scanCode=0))
            c.process(0x100, SimpleNamespace(flags=0, vkCode=8, scanCode=0))
            self.assertEqual("".join(c.buffer), "a^")
            self.h.text_composer.feed("^", True)
            c.process(0x100, SimpleNamespace(flags=0, vkCode=8, scanCode=0))
            self.assertEqual("".join(c.buffer), "a^")
            self.h.text_composer.feed("^", True)
            c.process(0x100, SimpleNamespace(flags=0, vkCode=27, scanCode=0))
            self.assertEqual(c.buffer, [])
            self.assertEqual(self.h.text_composer.pending, "")

    def test_submission_and_clipboard_preserve_carets(self):
        c = self.c
        with patch.object(c, "post", return_value={"request_id": "caret", "answer": "x^2 + SO4^2-"}) as post, \
             patch.object(c.pyperclip, "copy") as copy:
            c.submit("2^3; x^2 + y^2; SO4^2-", request_id="caret")
        self.assertEqual(post.call_args.args[1]["q"], "2^3; x^2 + y^2; SO4^2-")
        copy.assert_called_once_with("x^2 + SO4^2-")
