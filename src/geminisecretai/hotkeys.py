"""Windows keyboard hook and layout-aware text translation."""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import unicodedata
log = logging.getLogger("gemini_hotkey")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.c_void_p]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes = [ctypes.c_uint]
user32.ToUnicodeEx.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
MODIFIERS = {"ctrl": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C)}
PASS_KEYS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x14, 0x90, 0x91}
WH_KEYBOARD_LL, WM_QUIT = 13, 0x0012


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


def down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class DeadKeyComposer:
    # ToUnicodeEx returns spacing accents. Keep composition private to recording;
    # never mutate Windows' shared dead-key buffer to translate swallowed input.
    ACCENTS = dict(zip("^´`~¨˚ˇ˘˙˝˛¸¯", "\u0302\u0301\u0300\u0303\u0308\u030a\u030c\u0306\u0307\u030b\u0328\u0327\u0304"))

    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = ""
        self.layout = None

    def backspace(self):
        if self.pending:
            self.pending = ""
            return True
        return False

    def feed(self, text, dead=False, layout=None):
        if layout != self.layout:
            self.pending = ""
            self.layout = layout
        if not text:
            return ""
        accent, self.pending = self.pending, ""
        if dead:
            if accent == text:
                return accent
            self.pending = text
            return accent
        if not accent:
            return text
        if text == " ":
            return accent
        mark = self.ACCENTS.get(accent)
        composed = unicodedata.normalize("NFC", text[0] + mark) if mark else ""
        if len(composed) == 1:
            return composed + text[1:]
        return accent + text


text_composer = DeadKeyComposer()


def translate_key(vk, scan):
    status = (ctypes.c_ubyte * 256)()
    for key in range(256):
        if down(key):
            status[key] = 0x80
    status[vk] |= 0x80  # Low-level hook runs before asynchronous state updates.
    for key in (0x14, 0x90, 0x91):
        status[key] |= user32.GetKeyState(key) & 1
    if status[0xA5] & 0x80:  # AltGr layouts use Ctrl+Alt translation tables.
        status[0x11] |= 0x80
        status[0x12] |= 0x80
    buffer = ctypes.create_unicode_buffer(8)
    thread_id = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    layout = user32.GetKeyboardLayout(thread_id)
    length = user32.ToUnicodeEx(vk, scan, status, buffer, 8, 4, layout)
    text = buffer[:abs(length)] if length else ""
    return text_composer.feed(text, dead=length < 0, layout=layout)


class KeyboardHook:
    """Small Windows low-level hook; avoids pynput's Python 3.14 incompatibility."""

    def __init__(self, handler):
        self.handler = handler
        self.hook = None
        self.thread_id = None
        self.callback = None
        self.ready = threading.Event()

    def start(self):
        self.thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("The global keyboard hook did not start.")

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

        @callback_type
        def callback(code, message, lparam):
            if code >= 0:
                try:
                    data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if self.handler(message, data):
                        return 1
                except Exception:
                    log.exception("Key filter failure")
            return user32.CallNextHookEx(self.hook, code, message, lparam)

        self.callback = callback  # Keep it alive for the lifetime of the hook.
        self.hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, ctypes.cast(self.callback, ctypes.c_void_p), kernel32.GetModuleHandleW(None), 0
        )
        if not self.hook:
            self.ready.set()
            log.error("SetWindowsHookExW failed: %s", ctypes.get_last_error())
            return
        self.ready.set()
        message = wt.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        user32.UnhookWindowsHookEx(self.hook)
        self.hook = None

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        if getattr(self, "thread", None):
            self.thread.join(timeout=2)
