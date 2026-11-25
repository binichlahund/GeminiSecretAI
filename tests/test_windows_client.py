"""Windows client checks with networking and browser startup disabled."""
import sys
import threading
import gc
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform.startswith("win"), "Windows-specific client")
class WindowsClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from geminisecretai import client as gemini_hotkey
        cls.client = gemini_hotkey

    def setUp(self):
        c = self.client
        c.shutdown.clear()
        saved_guard, saved_window = c.instance_guard, c.control_window
        c.instance_guard, c.control_window = None, None
        self.addCleanup(lambda: setattr(c, "instance_guard", saved_guard))
        self.addCleanup(lambda: setattr(c, "control_window", saved_window))
        saved = dict(c.cfg)
        saved_exit = c.EXIT_HOTKEY_MODS, c.EXIT_HOTKEY_VK
        self.addCleanup(c.shutdown.clear)
        self.addCleanup(lambda: c.cfg.update(saved))
        self.addCleanup(lambda: setattr(c, "EXIT_HOTKEY_MODS", saved_exit[0]))
        self.addCleanup(lambda: setattr(c, "EXIT_HOTKEY_VK", saved_exit[1]))
        c.state = c.IDLE
        c.buffer.clear()
        c.suppressed.clear()
        c.hotkey_latch.down.clear()
        c.hotkey_suppressed.clear()
        while not c.ui_queue.empty():
            c.ui_queue.get_nowait()

    def make_window(self):
        c = self.client
        root = c.ctk.CTk()
        root.withdraw()
        with patch.object(c.Window, "poll_bridge"):
            window = c.Window(root)
        root.withdraw()
        root.update_idletasks()
        window.bridge_status = {"mode": "run", "headless": True, "phase": "ready"}
        self.addCleanup(lambda: self.destroy_test_root(root))
        self.addCleanup(lambda: window.close_thread.join(2) if getattr(window, "close_thread", None) else None)
        return window

    def destroy_test_root(self, root):
        if root.winfo_exists():
            for callback in root.tk.splitlist(root.tk.call("after", "info")):
                # Cancel timers without deleting child widgets' registered Tcl
                # commands; their own destroy methods still own those commands.
                root.tk.call("after", "cancel", callback)
            root.destroy()
        gc.collect()

    def test_held_text_hotkey_does_not_send_and_modifiers_are_exact(self):
        c = self.client
        key = SimpleNamespace(flags=0, vkCode=32, scanCode=0)
        with patch.object(c, "down", lambda vk: vk in (0x11, 0x12)):
            c.process(0x100, key)
            c.process(0x100, key)
            self.assertEqual(c.state, c.RECORDING)
            c.process(0x101, key)
        with patch.object(c, "down", lambda vk: vk in (0x11, 0x12, 0x10)):
            c.process(0x100, key)
            self.assertEqual(c.state, c.RECORDING)

    def test_held_image_hotkey_queues_only_one_capture(self):
        c = self.client
        key = SimpleNamespace(flags=0, vkCode=ord("I"), scanCode=0)
        with patch.object(c, "down", lambda vk: vk in (0x11, 0x12)):
            for _ in range(10):
                c.process(0x100, key)
        items = []
        while not c.ui_queue.empty():
            items.append(c.ui_queue.get_nowait())
        self.assertEqual(items.count(("capture",)), 1)
        self.assertEqual(c.state, c.CAPTURING)

    def test_regular_character_repeat_still_edits_prompt(self):
        c = self.client
        c.state = c.RECORDING
        key = SimpleNamespace(flags=0, vkCode=ord("A"), scanCode=0)
        with patch.object(c, "down", return_value=False), patch.object(c, "translate_key", return_value="a"):
            c.process(0x100, key)
            c.process(0x100, key)
        self.assertEqual("".join(c.buffer), "aa")
        key.vkCode = 32
        with patch.object(c, "down", return_value=False), patch.object(c, "translate_key", return_value=" "):
            c.process(0x100, key)
            c.process(0x100, key)
        self.assertEqual("".join(c.buffer), "aa  ")

    def test_single_instance_and_restart_signal(self):
        from geminisecretai.instance import InstanceGuard
        a = InstanceGuard("tests-only-unique-root", "test")
        b = InstanceGuard("tests-only-unique-root", "test")
        try:
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            b.api.SetEvent(b.event)
            self.assertTrue(a.restart_requested())
            a.close()
            self.assertTrue(b.acquire())
            self.assertFalse(b.restart_requested())
        finally:
            a.close()
            b.close()

    def test_ui_constructs_with_prompt_controls_without_starting_browser(self):
        c = self.client
        c.ctk.set_appearance_mode("dark")
        root = c.ctk.CTk()
        root.withdraw()
        try:
            with patch.object(c.Window, "poll_bridge"), patch.object(c.Window, "refresh_status"):
                window = c.Window(root)
                root.withdraw()
                root.update_idletasks()
                self.assertEqual(window.system_prompt.get("1.0", "end-1c"), c.cfg["system_prompt"])
                self.assertEqual(window.prompt_enabled.get(), c.cfg["system_prompt_enabled"])
        finally:
            root.destroy()

    def test_exit_hotkey_is_debounced_and_blocks_other_shortcuts(self):
        c = self.client
        key = SimpleNamespace(flags=0, vkCode=0x7B, scanCode=0)  # F12
        with patch.object(c, "down", lambda vk: vk in (0x11, 0x12)):
            for _ in range(10):
                c.process(0x100, key)
            c.process(0x100, SimpleNamespace(flags=0, vkCode=32, scanCode=0))
        self.assertTrue(c.shutdown.is_set())
        items = []
        while not c.ui_queue.empty():
            items.append(c.ui_queue.get_nowait())
        self.assertEqual(items, [("quit",)])
        self.assertEqual(c.state, c.IDLE)

    def test_hide_withdraws_only_window_and_errors_do_not_restore_it(self):
        c = self.client
        window = self.make_window()
        c.state = c.THINKING
        window.hide_app()
        self.assertEqual(window.root.state(), "withdrawn")
        self.assertTrue(window.hidden)
        self.assertFalse(c.shutdown.is_set())
        window.set_message("Error: test", error=True)
        window.root.update_idletasks()
        self.assertEqual(window.root.state(), "withdrawn")
        self.assertIn(c.cfg["exit_hotkey"], window.exit_hint.cget("text"))

    def test_hidden_text_delivery_and_image_dispatch(self):
        c = self.client
        window = self.make_window()
        window.hide_app()
        with patch.object(c, "post", return_value={"request_id": "test", "answer": "4"}), \
             patch.object(c.pyperclip, "copy") as copy:
            c.submit("2+2", request_id="test")
        copy.assert_called_once_with("4")
        self.assertEqual(window.root.state(), "withdrawn")
        with patch.object(c, "down", lambda vk: vk in (0x11, 0x12)):
            c.process(0x100, SimpleNamespace(flags=0, vkCode=ord("I"), scanCode=0))
        self.assertEqual(c.state, c.CAPTURING)
        self.assertEqual(window.root.state(), "withdrawn")

    def test_hidden_capture_cancel_removes_overlay_without_restoring_root(self):
        c = self.client
        window = self.make_window()
        window.hide_app()
        with patch.object(c.display, "desktop_bounds", return_value=(0, 0, 1920, 1080)), \
             patch.object(c.display, "place"):
            window.capture_area()
        overlay = window.capture_overlay
        window.root.update()
        overlay.event_generate("<Escape>")
        window.root.update()
        self.assertIsNone(window.capture_overlay)
        self.assertEqual(c.state, c.IDLE)
        self.assertEqual(window.root.state(), "withdrawn")

    def test_dot_toggle_is_immediate_preserves_expiry_and_tracks_active_request(self):
        c = self.client
        window = self.make_window()
        root = Mock()
        with patch.object(c.tk, "Canvas"), patch.object(c.display, "place"), \
             patch.object(c.display, "active_work_area", return_value=(0, 0, 1920, 1040)), \
             patch.object(c.time, "monotonic", return_value=100):
            indicator = c.Dot(root)
            with patch.object(c, "dot", indicator, create=True), patch.object(c, "save_config"):
                c.cfg["dot"] = True
                indicator.show(c.COLORS["ok"], 1000)
                expiry = indicator.expires_at
                window.dot_enabled.set(False)
                window.toggle_dot()
                root.withdraw.assert_called()
                self.assertEqual(indicator.expires_at, expiry)
                window.dot_enabled.set(True)
                window.toggle_dot()
                self.assertEqual(indicator.expires_at, expiry)
                c.state = c.THINKING
                window.toggle_dot()
                self.assertEqual(indicator.color, c.COLORS["thinking"])
                self.assertIsNone(indicator.expires_at)
        with patch.object(c.time, "monotonic", return_value=103), patch.object(c.display, "place"):
            indicator.show(c.COLORS["ok"], 1)
        with patch.object(c.time, "monotonic", return_value=105):
            root.deiconify.reset_mock()
            indicator.refresh()
            root.deiconify.assert_not_called()

    def test_minimized_and_hidden_capture_complete_without_showing_controls(self):
        c = self.client
        window = self.make_window()
        window.root.deiconify()
        window.root.update()
        window.root.iconify()
        window.root.update()
        self.assertEqual(window.root.state(), "iconic")
        window.hide_app()
        with patch.object(c.display, "desktop_bounds", return_value=(0, 0, 1920, 1080)), \
             patch.object(c.display, "place"), \
             patch.object(c.display, "cursor_position", side_effect=[(10, 10), (110, 110)]):
            window.capture_area()
            window.root.update()
            canvas = window.capture_overlay.winfo_children()[0]
            canvas.event_generate("<ButtonPress-1>", x=10, y=10)
            canvas.event_generate("<ButtonRelease-1>", x=110, y=110)
            window.root.update()
        self.assertIsNone(window.capture_overlay)
        self.assertEqual(window.root.state(), "withdrawn")
        self.assertIsNotNone(window.capture_after_id)
        with patch.object(c, "ImageGrab") as grab, patch.object(c, "send_image") as send:
            window.save_capture(10, 10, 110, 110)
            grab.grab.assert_called_once_with(bbox=(10, 10, 110, 110), all_screens=True)
            send.assert_called_once()
        self.assertEqual(window.root.state(), "withdrawn")

    def test_keybind_ui_rejects_conflicts_and_activates_saved_exit(self):
        c = self.client
        window = self.make_window()
        with patch.object(c, "save_config") as save:
            window.exit_hotkey.delete(0, "end")
            window.exit_hotkey.insert(0, c.cfg["hotkey"])
            window.save_keybinds()
            save.assert_not_called()
            self.assertIn("different", window.hotkey_note.cget("text"))
            window.exit_hotkey.delete(0, "end")
            window.exit_hotkey.insert(0, "ctrl+alt+del")
            window.save_keybinds()
            save.assert_not_called()
            self.assertIn("reserved", window.hotkey_note.cget("text"))
            window.exit_hotkey.delete(0, "end")
            window.exit_hotkey.insert(0, "ctrl+shift+f11")
            window.save_keybinds()
            self.assertEqual(c.EXIT_HOTKEY_VK, 0x7A)
            self.assertIn("ctrl+shift+f11", window.exit_hint.cget("text"))
            save.assert_called_once()

    def test_exit_during_request_drops_late_answer_and_destroys_capture(self):
        c = self.client
        window = self.make_window()
        window.hide_app()
        entered, release = threading.Event(), threading.Event()
        def post(*args):
            entered.set()
            release.wait(2)
            return {"answer": "4", "request_id": "late"}
        c.state = c.THINKING
        overlay = Mock()
        window.capture_overlay = overlay
        window.capture_after_id = window.root.after(10000, lambda: None)
        with patch.object(c, "post", side_effect=post), \
             patch.object(c, "bridge_proc", None), \
             patch.object(c, "dot", Mock(), create=True) as indicator, \
             patch.object(c.pyperclip, "copy") as copy:
            worker = threading.Thread(target=c.submit, args=("2+2", None, False, "late"))
            worker.start()
            self.assertTrue(entered.wait(1))
            window.quit()
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            copy.assert_not_called()
            indicator.hide.assert_called_once()
        overlay.destroy.assert_called_once()
        self.assertIsNone(window.capture_after_id)
        self.assertTrue(c.shutdown.is_set())
        self.assertEqual(window.root.state(), "withdrawn")
        c.cancelled_requests.clear()

    def test_main_releases_hook_and_guard_for_clean_relaunch(self):
        c = self.client
        root = Mock()
        guard = Mock()
        guard.acquire.return_value = True
        hook = Mock(hook=1)
        with patch.object(c, "InstanceGuard", return_value=guard), \
             patch.object(type(c.PATHS), "prepare", return_value=c.PATHS), \
             patch.object(c.logging, "basicConfig"), \
             patch.object(c, "load_config", return_value=dict(c.cfg)), \
             patch.object(c.ctk, "CTk", return_value=root), \
             patch.object(c, "Dot"), patch.object(c.tk, "Toplevel"), \
             patch.object(c, "Window"), patch.object(c, "KeyboardHook", return_value=hook):
            c.main()
        hook.stop.assert_called_once()
        guard.close.assert_called_once()

    def test_navigation_keyboard_focus_and_scroll_reset(self):
        window = self.make_window()
        window.root.deiconify()
        window.root.geometry("440x440")
        window.show_page("Settings")
        window.root.update()
        canvas = window.pages["Settings"]._parent_canvas
        canvas.yview_moveto(1)
        self.assertGreater(canvas.yview()[0], 0)
        window.show_settings_section("Instructions")
        window.root.update_idletasks()
        self.assertEqual(canvas.yview()[0], 0)
        # The actual text labels inside CTk's canvas buttons participate in Tab.
        home = window.navigation._buttons_dict["Home"]
        self.assertTrue(home._text_label.cget("takefocus"))
        home._text_label.focus_force()
        window.root.update()
        home._text_label.event_generate("<Return>")
        self.assertEqual(window.active_page, "Home")
        window.show_page("Settings")
        window.show_settings_section("Shortcuts")
        window.root.update_idletasks()
        window.reveal_focus(window.exit_hotkey)
        self.assertGreater(canvas.yview()[0], 0)

    def test_inline_dirty_validation_and_saved_feedback(self):
        c = self.client
        window = self.make_window()
        window.mark_dirty("shortcuts")
        self.assertEqual(window.hotkey_note.cget("text"), "Unsaved changes")
        with patch.object(c, "save_config"):
            window.save_keybinds()
        self.assertIn("Saved", window.hotkey_note.cget("text"))
        for field, value in (("bridge_port", "80"), ("request_timeout", "240")):
            window.advanced_vars[field].delete(0, "end")
            window.advanced_vars[field].insert(0, value)
        with patch.object(c, "save_config") as save:
            window.save_advanced()
            save.assert_not_called()
        self.assertIn("1024", window.advanced_note.cget("text"))
        window.advanced_vars["bridge_port"].delete(0, "end")
        window.advanced_vars["bridge_port"].insert(0, "8012")
        with patch.object(c, "save_config") as save:
            window.save_advanced()
            save.assert_called_once()
        self.assertIn("Restart", window.advanced_note.cget("text"))

    def test_connection_and_request_states_are_separate(self):
        c = self.client
        window = self.make_window()
        window.bridge_status = {"phase": "checking_login", "headless": True}
        window.refresh_status()
        self.assertNotEqual(window.status_title.cget("text"), "Ready")
        self.assertEqual(window.capture_button.cget("state"), "disabled")
        window.bridge_status["phase"] = "ready"
        window.refresh_status()
        self.assertEqual(window.capture_button.cget("state"), "normal")
        c.state = c.THINKING
        window.bridge_status["phase"] = "uploading"
        window.refresh_status()
        self.assertEqual(window.status_title.cget("text"), "Connected")
        self.assertIn("Uploading", window.request_status.cget("text"))

    def test_small_window_stacks_actions_and_wraps_labels(self):
        window = self.make_window()
        window.root.deiconify()
        window.root.geometry("400x440")
        window.root.update()
        window.on_resize()
        self.assertEqual(window.capture_button.pack_info()["side"], "top")
        self.assertLessEqual(window.status_detail.cget("wraplength"), 320)
        self.assertLess(window.hide_button.winfo_rootx() + window.hide_button.winfo_width(),
                        window.root.winfo_rootx() + window.root.winfo_width())


if __name__ == "__main__":
    unittest.main()
