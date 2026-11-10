"""Windows hotkey client for the local Gemini browser bridge."""
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pyperclip
import tkinter as tk
from tkinter import filedialog
from tkinter import ttk
import customtkinter as ctk
from PIL import ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
logging.basicConfig(filename=os.path.join(HERE, "gemini_hotkey.log"), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gemini_hotkey")
CONFIG_PATH = os.path.join(HERE, "config.json")
DEFAULTS = {
    "hotkey": "ctrl+alt+space",
    "image_hotkey": "ctrl+alt+i",
    "system_prompt": "",
    "show_window_on_start": True,
    "dot": True,
    "suppress_keys": True,
    "bridge_port": 8001,
    "request_timeout": 300,
}


def load_config():
    config = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            config.update(json.load(file))
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not read config.json")
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)
    return config


cfg = load_config()
BRIDGE = f"http://127.0.0.1:{cfg['bridge_port']}"
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p
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


def parse_hotkey(value):
    parts = [x.strip().lower() for x in value.split("+") if x.strip()]
    modifiers, keys = [x for x in parts if x in MODIFIERS], [x for x in parts if x not in MODIFIERS]
    if len(keys) != 1:
        raise ValueError("Hotkey needs exactly one non-modifier key")
    key = keys[0]
    if key == "space":
        vk = 0x20
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"Unsupported hotkey: {value}")
    return modifiers, vk


try:
    HOTKEY_MODS, HOTKEY_VK = parse_hotkey(cfg["hotkey"])
except ValueError:
    HOTKEY_MODS, HOTKEY_VK = parse_hotkey(DEFAULTS["hotkey"])
try:
    IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = parse_hotkey(cfg["image_hotkey"])
except ValueError:
    IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = parse_hotkey(DEFAULTS["image_hotkey"])


def modifiers_held(modifiers):
    return all(any(down(vk) for vk in MODIFIERS[modifier]) for modifier in modifiers)


def translate_key(vk, scan):
    status = (ctypes.c_ubyte * 256)()
    for key in range(256):
        if down(key):
            status[key] = 0x80
    buffer = ctypes.create_unicode_buffer(8)
    thread_id = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    length = user32.ToUnicodeEx(vk, scan, status, buffer, 8, 4, user32.GetKeyboardLayout(thread_id))
    return buffer.value[:length] if length > 0 else ""


def bridge_up():
    try:
        urllib.request.urlopen(BRIDGE + "/health", timeout=1.5).read()
        return True
    except Exception:
        return False


def start_bridge():
    if bridge_up():
        return
    output = open(os.path.join(HERE, "gemini_bridge.log"), "ab")
    subprocess.Popen([sys.executable, "-m", "uvicorn", "gemini_server:app", "--host", "127.0.0.1", "--port", str(cfg["bridge_port"])], cwd=HERE, stdout=output, stderr=output, stdin=subprocess.DEVNULL, creationflags=0x08000000 | 0x00000200)


def post(path, data, timeout=300):
    request = urllib.request.Request(BRIDGE + path, data=json.dumps(data).encode("utf-8"), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("detail", str(error))
        except Exception:
            detail = str(error)
        raise RuntimeError(detail)


ui_queue = queue.Queue()
COLORS = {"record": "#e53935", "thinking": "#fb8c00", "ok": "#43a047", "error": "#8e24aa"}
IDLE, RECORDING, THINKING = "idle", "recording", "thinking"
state, buffer, suppressed = IDLE, [], set()


class Dot:
    def __init__(self, root):
        self.root, self.token = root, 0
        root.overrideredirect(True); root.attributes("-topmost", True); root.attributes("-transparentcolor", "black"); root.configure(bg="black")
        self.canvas = tk.Canvas(root, width=16, height=16, bg="black", highlightthickness=0); self.canvas.pack()
        self.oval = self.canvas.create_oval(2, 2, 14, 14, fill=COLORS["ok"], outline="")
        root.geometry("16x16-30-70"); root.withdraw()

    def show(self, color, milliseconds=None):
        self.token += 1
        if not cfg["dot"]:
            self.root.withdraw(); return
        self.canvas.itemconfig(self.oval, fill=color)
        self.root.deiconify()
        if milliseconds:
            token = self.token
            self.root.after(milliseconds, lambda: self.root.withdraw() if token == self.token else None)

    def hide(self):
        self.token += 1; self.root.withdraw()


def submit(question, image_path=None, delete_after=False):
    global state
    try:
        payload = {"q": question, "system_prompt": cfg["system_prompt"]}
        if image_path:
            payload["image_path"] = image_path
        answer = post("/ask", payload, cfg["request_timeout"])["answer"].strip()
        pyperclip.copy(answer)
        ui_queue.put(("dot", COLORS["ok"], 2500)); ui_queue.put(("result", "Answer copied to clipboard."))
    except Exception as exc:
        log.exception("Gemini query failed")
        ui_queue.put(("dot", COLORS["error"], 4000)); ui_queue.put(("result", f"Error: {exc}"))
    finally:
        if delete_after:
            try:
                os.remove(image_path)
            except OSError:
                pass
        state = IDLE


def toggle():
    global state
    if state == IDLE:
        buffer.clear(); state = RECORDING; ui_queue.put(("dot", COLORS["record"], None)); ui_queue.put(("result", "Recording prompt…"))
    elif state == RECORDING:
        question = "".join(buffer).strip(); buffer.clear()
        if not question:
            state = IDLE; ui_queue.put(("hide",)); return
        state = THINKING; ui_queue.put(("dot", COLORS["thinking"], None)); ui_queue.put(("result", "Gemini is thinking…"))
        threading.Thread(target=submit, args=(question,), daemon=True).start()


def send_image(image_path, delete_after=False):
    global state
    if state != IDLE or not image_path:
        return
    state = THINKING
    ui_queue.put(("dot", COLORS["thinking"], None))
    ui_queue.put(("result", "Gemini is analyzing the image…"))
    question = "Analyze the attached image. Answer the question or task shown in it. Reply concisely."
    threading.Thread(target=submit, args=(question, image_path, delete_after), daemon=True).start()


def process(message, data):
    global state
    if data.flags & 0x10:
        return False
    is_down, is_up, vk = message in (0x100, 0x104), message in (0x101, 0x105), data.vkCode
    if is_up:
        if vk in suppressed:
            suppressed.discard(vk); return True
        return False
    if not is_down:
        return False
    if vk == IMAGE_HOTKEY_VK and modifiers_held(IMAGE_HOTKEY_MODS):
        suppressed.add(vk)
        if state == IDLE:
            ui_queue.put(("capture",))
        return True
    if vk == HOTKEY_VK and modifiers_held(HOTKEY_MODS):
        suppressed.add(vk); toggle(); return True
    if state != RECORDING or vk in PASS_KEYS:
        return False
    if vk == 0x1B:
        buffer.clear(); state = IDLE; ui_queue.put(("hide",)); ui_queue.put(("result", "Prompt cancelled."))
    elif vk == 0x08:
        if buffer: buffer.pop()
    elif vk == 0x0D:
        buffer.append("\n")
    else:
        character = translate_key(vk, data.scanCode)
        if character and ord(character[0]) >= 32:
            buffer.append(character)
    if cfg["suppress_keys"]:
        suppressed.add(vk)
    return cfg["suppress_keys"]


class KeyboardHook:
    """Small Windows low-level hook; avoids pynput's Python 3.14 incompatibility."""

    def __init__(self):
        self.hook = None
        self.thread_id = None
        self.callback = None
        self.ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="keyboard-hook", daemon=True).start()
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
                    if process(message, data):
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


class Window:
    BG, SURFACE, SIDEBAR = "#111827", "#1b2435", "#171f2e"
    TEXT, MUTED, ACCENT = "#f8fafc", "#94a3b8", "#7c5cff"
    GREEN, ORANGE, RED = "#32d583", "#fdb022", "#f97066"

    def __init__(self, root):
        self.root = root
        root.title("Gemini Hotkey")
        root.geometry("860x760")
        root.minsize(860, 760)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        shell = ctk.CTkFrame(root, fg_color=self.BG, corner_radius=0)
        shell.pack(fill="both", expand=True)

        sidebar = ctk.CTkFrame(shell, width=235, fg_color=self.SIDEBAR, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        ctk.CTkLabel(sidebar, text="✦", width=44, height=44, corner_radius=14,
                     fg_color=self.ACCENT, text_color="white", font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=24, pady=(30, 14))
        ctk.CTkLabel(sidebar, text="Gemini Hotkey", text_color=self.TEXT,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=24)
        ctk.CTkLabel(sidebar, text="Your local AI shortcut", text_color=self.MUTED,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", padx=24, pady=(2, 30))

        self.status_dot = ctk.CTkLabel(sidebar, text="●", text_color=self.ORANGE, font=ctk.CTkFont(size=21))
        self.status_dot.pack(anchor="w", padx=24)
        self.status_title = ctk.CTkLabel(sidebar, text="Starting up", text_color=self.TEXT,
                                         font=ctk.CTkFont(size=14, weight="bold"))
        self.status_title.pack(anchor="w", padx=24)
        self.status_detail = ctk.CTkLabel(sidebar, text="Connecting to local bridge", text_color=self.MUTED,
                                          font=ctk.CTkFont(size=11), wraplength=175, justify="left")
        self.status_detail.pack(anchor="w", padx=24, pady=(2, 30))

        ctk.CTkLabel(sidebar, text="HOW IT WORKS", text_color="#64748b",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=24)
        for n, text in (("01", "Press your hotkey"), ("02", "Type your prompt"), ("03", "Press it again")):
            row = ctk.CTkFrame(sidebar, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=(13, 0))
            ctk.CTkLabel(row, text=n, text_color=self.ACCENT, font=ctk.CTkFont(size=11, weight="bold"), width=28).pack(side="left")
            ctk.CTkLabel(row, text=text, text_color=self.MUTED, font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkButton(sidebar, text="Quit app", command=self.quit, height=36, corner_radius=10,
                      fg_color="transparent", hover_color="#263146", border_width=1, border_color="#334155",
                      text_color=self.MUTED, font=ctk.CTkFont(size=12, weight="bold")).pack(side="bottom", fill="x", padx=24, pady=24)

        content = ctk.CTkFrame(shell, fg_color=self.BG, corner_radius=0)
        content.pack(side="left", fill="both", expand=True, padx=32, pady=30)
        top = ctk.CTkFrame(content, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text="Control centre", text_color=self.TEXT,
                     font=ctk.CTkFont(size=27, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(top, text="Configure your shortcut and standing instructions.", text_color=self.MUTED,
                     font=ctk.CTkFont(size=13)).pack(anchor="w", pady=(2, 20))

        self.message = ctk.CTkLabel(content, text="Starting local Gemini bridge…", anchor="w", justify="left",
                                    height=54, corner_radius=12, fg_color="#202b40", text_color=self.TEXT,
                                    font=ctk.CTkFont(size=13, weight="bold"), padx=18)
        self.message.pack(fill="x", pady=(0, 16))

        settings = ctk.CTkFrame(content, fg_color="transparent")
        settings.pack(fill="x")
        hotkey_card = self.card(settings)
        hotkey_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.card_title(hotkey_card, "KEYBINDS", "Text and image shortcuts")
        ctk.CTkLabel(hotkey_card, text="TEXT ANSWER", text_color=self.MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(0, 5))
        self.hotkey = ctk.CTkEntry(hotkey_card, height=42, corner_radius=10, fg_color="#111827",
                                   border_color="#3b4a67", text_color=self.TEXT, font=ctk.CTkFont(family="Consolas", size=14))
        self.hotkey.insert(0, cfg["hotkey"])
        self.hotkey.pack(fill="x", padx=18, pady=(0, 12))
        self.hotkey.bind("<Return>", lambda event: self.save_keybinds())
        ctk.CTkLabel(hotkey_card, text="IMAGE ANSWER", text_color=self.MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(0, 5))
        self.image_hotkey = ctk.CTkEntry(hotkey_card, height=42, corner_radius=10, fg_color="#111827",
                                         border_color="#3b4a67", text_color=self.TEXT, font=ctk.CTkFont(family="Consolas", size=14))
        self.image_hotkey.insert(0, cfg["image_hotkey"])
        self.image_hotkey.pack(fill="x", padx=18, pady=(0, 9))
        self.image_hotkey.bind("<Return>", lambda event: self.save_keybinds())
        self.hotkey_note = ctk.CTkLabel(hotkey_card, text="Image hotkey opens a screen-area capture. Both work while this window is minimized.",
                                        text_color=self.MUTED, font=ctk.CTkFont(size=10), wraplength=265, justify="left")
        self.hotkey_note.pack(anchor="w", padx=18)
        self.primary_button(hotkey_card, "Save keybinds", self.save_keybinds).pack(fill="x", padx=18, pady=(14, 18))

        connection_card = self.card(settings)
        connection_card.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.card_title(connection_card, "CONNECTION", "Sign in only when required")
        ctk.CTkLabel(connection_card, text="Gemini runs headlessly once connected.", text_color=self.MUTED,
                     font=ctk.CTkFont(size=12), wraplength=260, justify="left").pack(anchor="w", padx=18, pady=(0, 16))
        self.secondary_button(connection_card, "Open login window", lambda: self.mode(False)).pack(fill="x", padx=18, pady=(0, 9))
        self.primary_button(connection_card, "Run Gemini headlessly", lambda: self.mode(True)).pack(fill="x", padx=18, pady=(0, 18))
        ctk.CTkLabel(connection_card, text="IMAGE QUESTIONS", text_color=self.ACCENT,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(0, 7))
        image_row = ctk.CTkFrame(connection_card, fg_color="transparent")
        image_row.pack(fill="x", padx=18, pady=(0, 18))
        self.secondary_button(image_row, "Capture area", self.capture_area).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.secondary_button(image_row, "Upload image", self.choose_image).pack(side="left", fill="x", expand=True, padx=(4, 0))

        prompt_card = self.card(content)
        prompt_card.pack(fill="both", expand=True, pady=(16, 0))
        self.card_title(prompt_card, "SYSTEM PROMPT", "Instructions added before every Gemini request")
        self.system_prompt = ctk.CTkTextbox(prompt_card, height=120, corner_radius=10, fg_color="#111827",
                                             border_width=1, border_color="#3b4a67", text_color=self.TEXT,
                                             font=ctk.CTkFont(size=13), wrap="word")
        self.system_prompt.insert("1.0", cfg["system_prompt"])
        self.system_prompt.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        footer = ctk.CTkFrame(prompt_card, fg_color="transparent")
        footer.pack(fill="x", padx=18, pady=(0, 18))
        self.system_note = ctk.CTkLabel(footer, text="Leave blank to send only what you type.", text_color=self.MUTED,
                                        font=ctk.CTkFont(size=11))
        self.system_note.pack(side="left")
        self.primary_button(footer, "Save system prompt", self.save_system_prompt, width=155).pack(side="right")

        self.last_bridge_check = 0
        self.bridge_ok = False
        self.refresh_status()
        root.after(150, self.pump)

    def card(self, parent):
        return ctk.CTkFrame(parent, fg_color=self.SURFACE, corner_radius=16)

    def card_title(self, parent, eyebrow, title):
        ctk.CTkLabel(parent, text=eyebrow, text_color=self.ACCENT, font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(parent, text=title, text_color=self.TEXT, font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=18, pady=(0, 14))

    def primary_button(self, parent, text, command, width=None):
        return ctk.CTkButton(parent, text=text, command=command, width=width or 120, height=40, corner_radius=10,
                             fg_color=self.ACCENT, hover_color="#927dff", text_color="white",
                             font=ctk.CTkFont(size=12, weight="bold"))

    def secondary_button(self, parent, text, command):
        return ctk.CTkButton(parent, text=text, command=command, height=40, corner_radius=10,
                             fg_color="#26334c", hover_color="#334563", text_color=self.TEXT,
                             font=ctk.CTkFont(size=12, weight="bold"))

    def set_message(self, text, error=False):
        self.message.configure(text=text, text_color=self.RED if error else self.TEXT)

    def refresh_status(self):
        if time.monotonic() - self.last_bridge_check > 2.5:
            self.bridge_ok = bridge_up()
            self.last_bridge_check = time.monotonic()
        if state == RECORDING:
            title, detail, color = "Capturing prompt", "Type your prompt, then press the hotkey again.", self.RED
        elif state == THINKING:
            title, detail, color = "Gemini is thinking", "The answer will be copied to your clipboard.", self.ORANGE
        elif self.bridge_ok:
            title, detail, color = "Ready", "Gemini is connected and running headlessly.", self.GREEN
        else:
            title, detail, color = "Offline", "The local bridge is starting or unavailable.", self.RED
        self.status_dot.configure(text_color=color)
        self.status_title.configure(text=title)
        self.status_detail.configure(text=detail)

    def save_keybinds(self):
        global HOTKEY_MODS, HOTKEY_VK, IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK
        value = self.hotkey.get().strip().lower()
        image_value = self.image_hotkey.get().strip().lower()
        try:
            modifiers, key = parse_hotkey(value)
            image_modifiers, image_key = parse_hotkey(image_value)
        except ValueError as exc:
            self.hotkey_note.configure(text=f"Invalid hotkey: {exc}", text_color=self.RED)
            return
        if key == image_key and set(modifiers) == set(image_modifiers):
            self.hotkey_note.configure(text="Text and image hotkeys must be different.", text_color=self.RED)
            return
        HOTKEY_MODS, HOTKEY_VK = modifiers, key
        IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = image_modifiers, image_key
        cfg["hotkey"] = value
        cfg["image_hotkey"] = image_value
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(cfg, file, indent=2)
        self.hotkey_note.configure(text="Saved — both global hotkeys are active now.", text_color=self.GREEN)
        self.set_message("Keybinds updated.")

    def save_system_prompt(self):
        value = self.system_prompt.get("1.0", "end-1c").strip()
        cfg["system_prompt"] = value
        with open(CONFIG_PATH, "w", encoding="utf-8") as file:
            json.dump(cfg, file, indent=2)
        self.system_note.configure(text="Saved — included with every prompt." if value else "Cleared — only typed prompts are sent.",
                                   text_color=self.GREEN if value else self.MUTED)
        self.set_message("System prompt saved." if value else "System prompt cleared.")

    def mode(self, headless):
        self.set_message("Switching browser mode…")
        def work():
            try:
                post("/admin/mode", {"headless": headless}, 60)
                ui_queue.put(("result", "Gemini is ready in headless mode." if headless else "Chrome is open. Sign in, then run headlessly."))
            except Exception as exc:
                ui_queue.put(("result", f"Error: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def choose_image(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Choose an image for Gemini",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("All files", "*.*")],
        )
        if path:
            send_image(path)

    def capture_area(self):
        if state != IDLE:
            self.set_message("Wait for the current request to finish before capturing an image.")
            return
        self.set_message("Drag over the part of the screen you want Gemini to answer.")
        overlay = tk.Toplevel(self.root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.22)
        overlay.configure(bg="black")
        canvas = tk.Canvas(overlay, bg="black", cursor="crosshair", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        selection = {"start": None, "rect": None}

        def begin(event):
            selection["start"] = (event.x_root, event.y_root)
            selection["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#7c5cff", width=3)

        def move(event):
            if selection["start"]:
                x = selection["start"][0] - overlay.winfo_rootx()
                y = selection["start"][1] - overlay.winfo_rooty()
                canvas.coords(selection["rect"], x, y, event.x, event.y)

        def finish(event):
            if not selection["start"]:
                return
            x1, y1 = selection["start"]
            x2, y2 = event.x_root, event.y_root
            overlay.destroy()
            if abs(x2 - x1) < 8 or abs(y2 - y1) < 8:
                self.set_message("Capture cancelled — choose a larger area.")
                return
            self.root.after(150, lambda: self.save_capture(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))

        overlay.bind("<Escape>", lambda event: (overlay.destroy(), self.set_message("Capture cancelled.")))
        canvas.bind("<ButtonPress-1>", begin)
        canvas.bind("<B1-Motion>", move)
        canvas.bind("<ButtonRelease-1>", finish)

    def save_capture(self, x1, y1, x2, y2):
        try:
            folder = os.path.join(HERE, "captures")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"capture-{int(time.time() * 1000)}.png")
            ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True).save(path)
            send_image(path, delete_after=True)
        except Exception as exc:
            log.exception("Screen capture failed")
            self.set_message(f"Error: Could not capture the screen: {exc}", error=True)

    def quit(self):
        try:
            post("/admin/quit", {}, 2)
        except Exception:
            pass
        self.root.destroy()

    def pump(self):
        try:
            while True:
                item = ui_queue.get_nowait()
                if item[0] == "dot": dot.show(item[1], item[2])
                elif item[0] == "hide": dot.hide()
                elif item[0] == "capture": self.capture_area()
                elif item[0] == "result": self.set_message(item[1], item[1].startswith("Error:"))
        except queue.Empty:
            pass
        self.refresh_status()
        self.root.after(150, self.pump)


def main():
    start_bridge()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk(); global dot; dot = Dot(tk.Toplevel(root)); window = Window(root)
    hook = KeyboardHook()
    hook.start()
    if not hook.hook:
        raise RuntimeError("Could not install the global keyboard hook. See gemini_hotkey.log.")
    root.deiconify()
    root.mainloop()
    hook.stop()


if __name__ == "__main__":
    main()

