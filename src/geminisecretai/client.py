"""Windows hotkey client for the local Gemini browser bridge."""
import ctypes
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
import uuid

# Configure DPI before CustomTkinter/Tk initializes any windows or screen metrics.
from .display import (
    DisplayGeometry, enable_dpi_awareness, indicator_bounds,
)
enable_dpi_awareness()

import pyperclip
import tkinter as tk
import customtkinter as ctk
from PIL import ImageGrab
from .settings import DEFAULTS, DEFAULT_PROMPT, HotkeyLatch, load_config, parse_hotkey, save_config, validate_keybinds
from .instance import InstanceGuard

from .paths import PATHS
from .ui import ControlPanel
from .capture import CaptureActions
from .hotkeys import KeyboardHook, down, translate_key, text_composer, MODIFIERS, PASS_KEYS

HERE = str(PATHS.root)
log = logging.getLogger("gemini_hotkey")
CONFIG_PATH = str(PATHS.config)
cfg = dict(DEFAULTS)
BRIDGE = f"http://127.0.0.1:{cfg['bridge_port']}"
OWNER_TOKEN = uuid.uuid4().hex
bridge_proc = None
shutdown = threading.Event()
active_request = None
cancelled_requests = set()
instance_guard = None
control_window = None
delivery_lock = threading.Lock()
bridge_start_lock = threading.Lock()
display = DisplayGeometry()


try:
    HOTKEY_MODS, HOTKEY_VK = parse_hotkey(cfg["hotkey"])
except ValueError:
    HOTKEY_MODS, HOTKEY_VK = parse_hotkey(DEFAULTS["hotkey"])
try:
    IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = parse_hotkey(cfg["image_hotkey"])
except ValueError:
    IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = parse_hotkey(DEFAULTS["image_hotkey"])
EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK = parse_hotkey(cfg["exit_hotkey"])


def modifiers_held(modifiers):
    held = frozenset(name for name, keys in MODIFIERS.items() if any(down(vk) for vk in keys))
    return held == frozenset(modifiers)




def bridge_up():
    try:
        with urllib.request.urlopen(BRIDGE + "/health", timeout=1) as response:
            status = json.load(response)
        return status.get("app") == "GeminiSecretAI" and status.get("protocol") == 2 and os.path.normcase(status.get("root", "")) == os.path.normcase(HERE)
    except Exception:
        return False


def start_bridge():
    with bridge_start_lock:
        if not shutdown.is_set():
            _start_bridge()


def _start_bridge():
    global bridge_proc
    # Do not adopt or kill a process merely because it uses our configured port.
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", cfg["bridge_port"])) == 0:
            raise RuntimeError("Bridge port is occupied. Quit the old app/bridge or choose another bridge_port.")
    environment = dict(os.environ, GEMINI_OWNER_TOKEN=OWNER_TOKEN,
                       GEMINI_BRIDGE_PORT=str(cfg["bridge_port"]))
    with open(str(PATHS.logs / "gemini_bridge.log"), "ab") as output:
        bridge_proc = subprocess.Popen(
            [sys.executable, "-m", "geminisecretai.bridge"],
            cwd=HERE, env=environment, stdout=output, stderr=output,
            stdin=subprocess.DEVNULL, creationflags=0x08000000,
        )


def post(path, data, timeout=300):
    request = urllib.request.Request(BRIDGE + path, data=json.dumps(data).encode("utf-8"),
                                     headers={"content-type": "application/json", "x-gemini-owner": OWNER_TOKEN})
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
COLORS = {"record": "#d89a98", "thinking": "#c9b582", "ok": "#91b6a2", "error": "#d89a98"}
IDLE, RECORDING, THINKING, CAPTURING = "idle", "recording", "thinking", "capturing"
state, buffer, suppressed = IDLE, [], set()
hotkey_latch = HotkeyLatch()
hotkey_suppressed = set()


class Dot:
    def __init__(self, root):
        self.root, self.token = root, 0
        self.color, self.expires_at = None, None
        root.overrideredirect(True); root.attributes("-topmost", True); root.attributes("-transparentcolor", "black"); root.configure(bg="black")
        self.canvas = tk.Canvas(root, width=16, height=16, bg="black", highlightthickness=0); self.canvas.pack()
        self.oval = self.canvas.create_oval(2, 2, 14, 14, fill=COLORS["ok"], outline="")
        root.geometry("16x16"); root.withdraw()

    def show(self, color, milliseconds=None):
        self.token += 1
        self.color = color
        self.expires_at = time.monotonic() + milliseconds / 1000 if milliseconds is not None else None
        self.refresh()
        if milliseconds:
            token = self.token
            self.root.after(milliseconds, lambda: self.hide() if token == self.token else None)

    def refresh(self):
        expired = self.expires_at is not None and time.monotonic() >= self.expires_at
        if shutdown.is_set() or not cfg["dot"] or self.color is None or expired:
            self.root.withdraw()
            return
        self.canvas.itemconfig(self.oval, fill=self.color)
        bounds = indicator_bounds(display.active_work_area())
        self.root.deiconify()
        display.place(self.root, bounds, no_activate=True)

    def hide(self):
        self.token += 1
        self.color, self.expires_at = None, None
        self.root.withdraw()


def start_query(question, image_path=None, delete_after=False):
    global active_request
    if shutdown.is_set():
        if delete_after and image_path:
            os.remove(image_path)
        return
    active_request = str(uuid.uuid4())
    threading.Thread(target=submit, args=(question, image_path, delete_after, active_request), daemon=True).start()


def submit(question, image_path=None, delete_after=False, request_id=None):
    global state, active_request
    started = time.monotonic()
    try:
        if shutdown.is_set() or request_id in cancelled_requests:
            return
        payload = {"q": question, "system_prompt": cfg["system_prompt"] if cfg["system_prompt_enabled"] else "",
                   "request_id": request_id, "timeout_seconds": max(5, cfg["request_timeout"] - 10)}
        if image_path:
            payload["image_path"] = image_path
        result = post("/ask", payload, cfg["request_timeout"])
        if result.get("request_id") != request_id:
            raise RuntimeError("Bridge returned a result for a different request.")
        if request_id in cancelled_requests or shutdown.is_set():
            return
        answer = result["answer"].strip()
        if not answer:
            raise RuntimeError("Gemini returned an empty answer.")
        delivery = time.monotonic()
        with delivery_lock:
            if shutdown.is_set() or request_id in cancelled_requests:
                return
            pyperclip.copy(answer)
        log.info("request=%s clipboard_seconds=%.3f end_to_end_seconds=%.3f stages=%s",
                 request_id, time.monotonic() - delivery, time.monotonic() - started, result.get("timings"))
        ui_queue.put(("dot", COLORS["ok"], 2500)); ui_queue.put(("result", "Answer copied to clipboard."))
    except Exception as exc:
        log.exception("Gemini query failed")
        # Ensure a timed-out HTTP client cannot leave a generating page behind.
        try:
            post("/cancel", {"request_id": request_id}, 5)
        except Exception:
            log.info("Cancellation unavailable; owned-service lease provides fallback cleanup.")
        if request_id not in cancelled_requests and not shutdown.is_set():
            ui_queue.put(("dot", COLORS["error"], 4000)); ui_queue.put(("result", f"Error: {exc}"))
    finally:
        if delete_after:
            try:
                os.remove(image_path)
            except OSError:
                pass
        state = IDLE
        active_request = None
        cancelled_requests.discard(request_id)


def toggle():
    global state
    if shutdown.is_set():
        return
    if state == IDLE:
        text_composer.reset()
        buffer.clear(); state = RECORDING; ui_queue.put(("dot", COLORS["record"], None)); ui_queue.put(("result", "Recording prompt…"))
    elif state == RECORDING:
        text_composer.reset()
        question = "".join(buffer).strip(); buffer.clear()
        if not question:
            state = IDLE; ui_queue.put(("hide",)); return
        state = THINKING; ui_queue.put(("dot", COLORS["thinking"], None)); ui_queue.put(("result", "Gemini is thinking…"))
        start_query(question)


def send_image(image_path, delete_after=False):
    global state
    if shutdown.is_set() or state not in (IDLE, CAPTURING) or not image_path:
        if delete_after and image_path:
            os.remove(image_path)
        return
    state = THINKING
    ui_queue.put(("dot", COLORS["thinking"], None))
    ui_queue.put(("result", "Gemini is analyzing the image…"))
    question = "Analyze the attached image. Answer the question or task shown in it. Reply concisely."
    start_query(question, image_path, delete_after)


def process(message, data):
    global state
    if data.flags & 0x10:
        return False
    is_down, is_up, vk = message in (0x100, 0x104), message in (0x101, 0x105), data.vkCode
    if is_up:
        hotkey_latch.release(vk)
        hotkey_suppressed.discard(vk)
        if vk in suppressed:
            suppressed.discard(vk); return True
        return False
    if not is_down:
        return False
    held = frozenset(name for name, keys in MODIFIERS.items() if any(down(key) for key in keys))
    if vk in hotkey_suppressed:
        return True
    action = hotkey_latch.match(vk, held, {
        "image": (IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK), "text": (HOTKEY_MODS, HOTKEY_VK),
        "exit": (EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK),
    })
    if action == "exit":
        hotkey_suppressed.add(vk)
        suppressed.add(vk)
        # Block delivery/requests immediately, before Tk drains the quit event.
        with delivery_lock:
            shutdown.set()
        ui_queue.put(("quit",))
        return True
    if shutdown.is_set():
        return False
    if action == "image":
        hotkey_suppressed.add(vk)
        suppressed.add(vk)
        if state == IDLE:
            state = CAPTURING
            ui_queue.put(("capture",))
        return True
    if action == "text":
        hotkey_suppressed.add(vk)
        suppressed.add(vk); toggle(); return True
    if state != RECORDING or vk in PASS_KEYS:
        return False
    if vk == 0x1B:
        text_composer.reset()
        buffer.clear(); state = IDLE; ui_queue.put(("hide",)); ui_queue.put(("result", "Prompt cancelled."))
    elif vk == 0x08:
        if not text_composer.backspace() and buffer:
            buffer.pop()
    elif vk == 0x0D:
        text_composer.reset()
        buffer.append("\n")
    else:
        character = translate_key(vk, data.scanCode)
        if character and ord(character[0]) >= 32:
            buffer.extend(character)
    if cfg["suppress_keys"]:
        suppressed.add(vk)
    return cfg["suppress_keys"]




class Window(ControlPanel, CaptureActions):
    def __init__(self, root):
        super().__init__(root, sys.modules[__name__])

    def set_message(self, text, error=False):
        self.message.configure(text=text[:220], text_color=self.RED if error else self.MUTED)
        if self.hidden and error:
            log.error("Hidden app: %s", text)


    def exit_hint_text(self):
        return f"When hidden, exit with {cfg['exit_hotkey']} or Task Manager. Relaunch to show settings."


    def hide_app(self):
        if self.closing or shutdown.is_set():
            return
        if not self.bridge_status.get("headless"):
            self.set_message("Run Gemini headlessly before hiding the app.")
            return
        self.hidden = True
        self.root.withdraw()
        log.info("Control window hidden; exit hotkey=%s", cfg["exit_hotkey"])


    def toggle_dot(self):
        updated = dict(cfg, dot=self.dot_enabled.get())
        try:
            save_config(CONFIG_PATH, updated)
        except OSError as exc:
            self.dot_enabled.set(cfg["dot"])
            self.set_message(f"Error saving status dot setting: {exc}", error=True)
            return
        cfg.update(updated)
        if cfg["dot"] and state in (RECORDING, THINKING):
            dot.show(COLORS["record"] if state == RECORDING else COLORS["thinking"])
        else:
            # Retain a result's original expiry when toggling it off/on.
            dot.refresh()


    def refresh_status(self):
        phase = self.bridge_status.get("phase", "starting")
        title, detail, color = {
            "ready": ("Ready", "Signed in. Gemini is running in the background.", self.GREEN),
            "login_required": ("Sign in required", "Open login, sign in, then choose Run headlessly.", self.ORANGE),
            "checking_login": ("Checking sign-in", "Waiting for Gemini. Open login if verification is needed.", self.ORANGE),
            "starting": ("Starting", "Connecting to Gemini…", self.ORANGE),
            "preparing": ("Checking Gemini", "Preparing your next question.", self.ORANGE),
            "disconnected": ("Disconnected", "Choose Run headlessly to reconnect.", self.ORANGE),
            "cancelled": ("Connected", "The last question was cancelled.", self.MUTED),
        }.get(phase, ("Connected", "Your Gemini session is in use.", self.GREEN) if phase in
              ("uploading", "sending", "generating", "extracting") else
              ("Connection needs attention", self.bridge_status.get("error") or "Try reconnecting or opening login.", self.RED))
        if phase == "ready" and not self.bridge_status.get("headless"):
            detail = "Signed in. The browser window is visible."
        self.status_title.configure(text=title, text_color=color)
        self.status_detail.configure(text=detail[:180])
        request_text = {
            RECORDING: "Recording · Type your question, then press the text shortcut again.",
            CAPTURING: "Selecting · Drag across a question. Esc cancels.",
            THINKING: {"uploading": "Uploading image…", "sending": "Sending question…",
                       "generating": "Gemini is answering…", "extracting": "Reading the answer…"}.get(phase, "Preparing question…"),
        }.get(state, "No question in progress. Answers are copied to your clipboard.")
        self.request_status.configure(text=request_text)
        ready = state == IDLE and phase == "ready" and not self.closing
        for button in (self.capture_button, self.upload_button):
            button.configure(state="normal" if ready else "disabled")
        self.cancel_button.configure(state="normal" if state != IDLE and not self.closing else "disabled")
        for button in (self.login_button, self.run_button):
            button.configure(state="normal" if state == IDLE and not self.closing else "disabled")


    def poll_bridge(self):
        if shutdown.is_set():
            return
        try:
            start_bridge()
        except Exception as exc:
            log.exception("Could not start bridge")
            ui_queue.put(("bridge_status", {"phase": "failed", "error": str(exc)}))
            ui_queue.put(("result", f"Error: {exc}"))
            return
        while not shutdown.is_set():
            try:
                request = urllib.request.Request(BRIDGE + "/status", headers={"x-gemini-owner": OWNER_TOKEN})
                with urllib.request.urlopen(request, timeout=2) as response:
                    status = json.load(response)
                ui_queue.put(("bridge_status", status))
            except Exception as exc:
                status = {"phase": "starting" if bridge_proc and bridge_proc.poll() is None else "failed",
                          "error": "Local bridge is unavailable."}
                ui_queue.put(("bridge_status", status))
            shutdown.wait(1)


    def save_keybinds(self):
        global HOTKEY_MODS, HOTKEY_VK, IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK, EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK
        value = self.hotkey.get().strip().lower()
        image_value = self.image_hotkey.get().strip().lower()
        exit_value = self.exit_hotkey.get().strip().lower()
        updated = dict(cfg, hotkey=value, image_hotkey=image_value, exit_hotkey=exit_value)
        try:
            bindings = validate_keybinds(updated)
        except ValueError as exc:
            self.hotkey_note.configure(text=f"Invalid hotkey: {exc}", text_color=self.RED)
            return
        try:
            save_config(CONFIG_PATH, updated)
        except OSError as exc:
            self.set_message(f"Error saving keybinds: {exc}", error=True)
            return
        HOTKEY_MODS, HOTKEY_VK = bindings["hotkey"]
        IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = bindings["image_hotkey"]
        EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK = bindings["exit_hotkey"]
        cfg.update(updated)
        self.hotkey_note.configure(text="Saved — all three shortcuts are active now.", text_color=self.GREEN)
        self.update_shortcuts()
        self.set_message("Keybinds updated.")


    def save_system_prompt(self):
        value = self.system_prompt.get("1.0", "end-1c").strip() or DEFAULT_PROMPT
        updated = dict(cfg, system_prompt=value, system_prompt_enabled=self.prompt_enabled.get())
        try:
            save_config(CONFIG_PATH, updated)
        except OSError as exc:
            self.set_message(f"Error saving prompt: {exc}", error=True)
            return
        cfg.update(updated)
        self.system_note.configure(text="Saved — applied to text and images." if cfg["system_prompt_enabled"] else "Saved — instructions disabled.",
                                   text_color=self.GREEN)
        self.set_message("System prompt settings saved.")


    def restore_system_prompt(self):
        self.system_prompt.delete("1.0", "end")
        self.system_prompt.insert("1.0", DEFAULT_PROMPT)
        self.prompt_enabled.set(True)
        self.save_system_prompt()

    def save_advanced(self):
        try:
            port = int(self.advanced_vars["bridge_port"].get())
            timeout = int(self.advanced_vars["request_timeout"].get())
            if not 1024 <= port <= 65535 or not 10 <= timeout <= 3600:
                raise ValueError("Use a port from 1024–65535 and a timeout from 10–3600 seconds.")
            updated = dict(cfg, bridge_port=port, request_timeout=timeout,
                           suppress_keys=self.suppress_var.get())
            save_config(CONFIG_PATH, updated)
        except (ValueError, OSError) as exc:
            self.advanced_note.configure(text=str(exc), text_color=self.RED)
            return
        cfg.update(updated)
        self.advanced_note.configure(text="Saved. Restart the app to apply connection changes.", text_color=self.GREEN)

    def open_location(self, path):
        try:
            if path == PATHS.logs:
                path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except OSError as exc:
            self.set_message(f"Could not open that location: {exc}", error=True)


    def cancel_current(self):
        global state
        if state == RECORDING:
            text_composer.reset()
            buffer.clear()
            state = IDLE
            dot.hide()
            self.set_message("Recording cancelled.")
        elif getattr(self, "capture_overlay", None) is not None:
            self.capture_overlay.event_generate("<Escape>")
        elif active_request:
            request_id = active_request
            cancelled_requests.add(request_id)
            self.set_message("Cancelling question…")
            def work():
                try:
                    post("/cancel", {"request_id": request_id}, 8)
                    ui_queue.put(("result", "Question cancelled."))
                    ui_queue.put(("hide",))
                except Exception as exc:
                    ui_queue.put(("result", f"Error cancelling: {exc}"))
            threading.Thread(target=work, daemon=True).start()


    def mode(self, headless):
        if self.closing or shutdown.is_set():
            return
        if state != IDLE:
            self.set_message("Cancel the current question before switching browser mode.")
            return
        self.set_message("Switching browser mode…")
        def work():
            try:
                post("/admin/mode", {"headless": headless}, 60)
                ui_queue.put(("result", "Gemini is ready in headless mode." if headless else "Chrome is open. Sign in, then run headlessly."))
            except Exception as exc:
                ui_queue.put(("result", f"Error: {exc}"))
        threading.Thread(target=work, daemon=True).start()


    def quit(self):
        global state
        if self.closing:
            return
        self.closing = True
        text_composer.reset()
        with delivery_lock:
            shutdown.set()
        buffer.clear()
        state = IDLE
        if active_request:
            cancelled_requests.add(active_request)
        if self.capture_after_id is not None:
            self.root.after_cancel(self.capture_after_id)
            self.capture_after_id = None
        overlay = getattr(self, "capture_overlay", None)
        if overlay is not None:
            overlay.destroy()
            self.capture_overlay = None
        dot.hide()
        self.set_message("Closing Gemini…")
        def close():
            try:
                with bridge_start_lock:
                    owned_bridge = bridge_proc
                if owned_bridge:
                    post("/admin/quit", {}, 4)
                    owned_bridge.wait(timeout=8)
            except Exception:
                log.exception("Graceful bridge shutdown failed")
                if bridge_proc and bridge_proc.poll() is None:
                    bridge_proc.terminate()  # Only our own child process.
                    try:
                        bridge_proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        log.error("Owned bridge has not exited yet")
            finally:
                ui_queue.put(("closed",))
        self.close_thread = threading.Thread(target=close, name="bridge-shutdown", daemon=True)
        self.close_thread.start()


    def pump(self):
        global state
        try:
            while True:
                item = ui_queue.get_nowait()
                if item[0] == "quit":
                    self.quit()
                    continue
                if shutdown.is_set() and item[0] != "closed":
                    continue
                if item[0] == "dot": dot.show(item[1], item[2])
                elif item[0] == "hide": dot.hide()
                elif item[0] == "capture": self.capture_area()
                elif item[0] == "bridge_status": self.bridge_status = item[1]
                elif item[0] == "closed":
                    self.root.destroy()
                    return
                elif item[0] == "result": self.set_message(item[1], item[1].startswith("Error:"))
        except queue.Empty:
            pass
        except Exception as exc:
            log.exception("UI event failed")
            self.set_message(f"Error: {exc}", error=True)
            if state == CAPTURING:
                overlay = getattr(self, "capture_overlay", None)
                if overlay:
                    overlay.destroy()
                    self.capture_overlay = None
                state = IDLE
        if instance_guard and instance_guard.restart_requested():
            self.quit()
        self.refresh_status()
        self.root.after(150, self.pump)




def main():
    global cfg, BRIDGE, instance_guard, control_window, HOTKEY_MODS, HOTKEY_VK, IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK, EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK
    instance_guard = InstanceGuard(HERE)
    if not instance_guard.acquire(restart="--restart" in sys.argv):
        instance_guard.close()
        ctypes.windll.user32.MessageBoxW(None, "GeminiSecretAI is already running. Use its configured exit hotkey or Task Manager, then relaunch to show settings.", "GeminiSecretAI", 0)
        return
    PATHS.prepare()
    logging.basicConfig(filename=str(PATHS.logs / "gemini_hotkey.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", force=True)
    cfg = load_config(CONFIG_PATH)
    BRIDGE = f"http://127.0.0.1:{cfg['bridge_port']}"
    HOTKEY_MODS, HOTKEY_VK = parse_hotkey(cfg["hotkey"])
    IMAGE_HOTKEY_MODS, IMAGE_HOTKEY_VK = parse_hotkey(cfg["image_hotkey"])
    EXIT_HOTKEY_MODS, EXIT_HOTKEY_VK = parse_hotkey(cfg["exit_hotkey"])
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk(); global dot; dot = Dot(tk.Toplevel(root)); window = Window(root)
    control_window = window
    hook = KeyboardHook(process)
    hook.start()
    if not hook.hook:
        raise RuntimeError("Could not install the global keyboard hook. See gemini_hotkey.log.")
    root.deiconify()
    try:
        root.mainloop()
    finally:
        shutdown.set()
        if getattr(window, "close_thread", None):
            window.close_thread.join(timeout=1)
        hook.stop()
        instance_guard.close()


def run():
    try:
        main()
    except Exception as exc:
        log.exception("Startup failed")
        if control_window is None or not control_window.hidden:
            ctypes.windll.user32.MessageBoxW(None, str(exc), "GeminiSecretAI could not start", 0x10)
