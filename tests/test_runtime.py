import asyncio
import tempfile
from pathlib import Path
import unittest

from geminisecretai.settings import DEFAULTS, DEFAULT_PROMPT, HotkeyLatch, load_config, normalize_config, parse_hotkey, save_config, validate_keybinds
from geminisecretai.browser import Completion
from geminisecretai.requests import RequestManager


class SettingsTests(unittest.TestCase):
    def test_prompt_migration_preserves_custom_and_explicit_disable(self):
        self.assertEqual(normalize_config({})["system_prompt"], DEFAULT_PROMPT)
        self.assertEqual(normalize_config({"system_prompt": ""})["system_prompt"], DEFAULT_PROMPT)
        self.assertEqual(normalize_config({"system_prompt": "My prompt"})["system_prompt"], "My prompt")
        self.assertFalse(normalize_config({"system_prompt": "", "system_prompt_enabled": False})["system_prompt_enabled"])

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = normalize_config({"system_prompt": "Only CH4", "system_prompt_enabled": False})
            save_config(path, config)
            self.assertEqual(load_config(path), config)

    def test_hotkey_repeat_exact_modifiers_and_rebinding(self):
        latch = HotkeyLatch()
        bindings = {"text": parse_hotkey("ctrl+alt+space"), "image": parse_hotkey("ctrl+alt+shift+space")}
        self.assertEqual(latch.match(32, {"ctrl", "alt"}, bindings), "text")
        self.assertIsNone(latch.match(32, {"ctrl", "alt"}, bindings))
        latch.release(32)
        self.assertEqual(latch.match(32, {"ctrl", "alt", "shift"}, bindings), "image")
        latch.release(32)
        self.assertIsNone(latch.match(32, {"ctrl", "alt", "win"}, bindings))
        for invalid in ("ctrl+ctrl+a", "ctrl++a", "ctrl+æ", "ctrl+alt", "ctrl+f25"):
            with self.assertRaises(ValueError):
                parse_hotkey(invalid)

    def test_completion_never_finishes_during_generation_pause(self):
        completion = Completion(4)
        completion.update("CH", True, False, now=0)
        self.assertFalse(completion.update("CH", True, False, now=10))
        self.assertFalse(completion.update("CH4", False, True, now=11))
        self.assertTrue(completion.update("CH4", False, True, now=11.5))

    def test_fallback_and_empty_completion(self):
        completion = Completion(4)
        self.assertFalse(completion.update("", False, True, now=0))
        self.assertFalse(completion.update("4", False, False, now=1))
        self.assertFalse(completion.update("4", False, False, now=2))
        self.assertTrue(completion.update("4", False, False, now=5))

    def test_exit_shortcut_migration_and_no_silent_substitution(self):
        self.assertEqual(normalize_config({})["exit_hotkey"], "ctrl+alt+f12")
        for key in ("hotkey", "image_hotkey", "exit_hotkey"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_config({key: "ctrl+alt+del"})
        for other in ("hotkey", "image_hotkey"):
            with self.subTest(other=other), self.assertRaisesRegex(ValueError, "must be different"):
                validate_keybinds(dict(DEFAULTS, exit_hotkey=DEFAULTS[other]))
        for reserved in ("ctrl+alt+delete", "alt+f4", "ctrl+shift+esc", "win+l"):
            with self.subTest(reserved=reserved), self.assertRaises(ValueError):
                parse_hotkey(reserved)

    def test_exit_and_dot_settings_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config = normalize_config({"exit_hotkey": "ctrl+shift+f11", "dot": False})
            save_config(path, config)
            loaded = load_config(path)
            self.assertEqual(loaded["exit_hotkey"], "ctrl+shift+f11")
            self.assertFalse(loaded["dot"])


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_request_sent_only_once(self):
        manager = RequestManager()
        calls = []
        async def work():
            calls.append(True)
            return "4"
        self.assertEqual(await manager.execute("same", work, 1), "4")
        with self.assertRaises(ValueError):
            await manager.execute("same", work, 1)
        self.assertEqual(len(calls), 1)

    async def test_timeout_releases_queue_and_waits_for_cleanup(self):
        manager = RequestManager()
        cleaned = []
        async def hangs():
            try:
                await asyncio.sleep(10)
            finally:
                await asyncio.sleep(.01)
                cleaned.append(True)
        with self.assertRaises(TimeoutError):
            await manager.execute("hang", hangs, .03)
        self.assertTrue(cleaned)
        self.assertFalse(manager.lock.locked())
        self.assertEqual(await manager.execute("next", lambda: asyncio.sleep(0, result="4"), 1), "4")

    async def test_queue_deadline_and_cancel_do_not_release_another_lock(self):
        manager = RequestManager(queue_timeout=.02)
        entered = asyncio.Event()
        async def first():
            entered.set()
            await asyncio.sleep(10)
        one = asyncio.create_task(manager.execute("one", first, 5))
        await entered.wait()
        with self.assertRaises(TimeoutError):
            await manager.execute("two", lambda: asyncio.sleep(0), 1)
        self.assertTrue(manager.lock.locked())
        await manager.cancel("one")
        await asyncio.gather(one, return_exceptions=True)
        self.assertFalse(manager.active)
        self.assertFalse(manager.lock.locked())

    async def test_cancel_before_registration_prevents_submission(self):
        manager = RequestManager()
        await manager.cancel("before")
        with self.assertRaises(ValueError):
            await manager.execute("before", lambda: asyncio.sleep(0), 1)


if __name__ == "__main__":
    unittest.main()
