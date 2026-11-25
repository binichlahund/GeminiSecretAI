"""Configuration and hotkey rules shared by the UI and regression tests."""
import json
import os
from pathlib import Path

DEFAULT_PROMPT = """Answer the user's question or the task shown in the attached image.

Return only the final answer, once. Do not include greetings, introductions, explanations, reasoning, headings, filenames, image IDs, attachment references, or closing remarks.

Use plain text. Do not use Markdown, LaTeX, code fences, or duplicate representations of the same formula.

For mathematics and chemistry, use readable notation such as:
CH4, H2O, SO4^2-, x^2, and 2H2 + O2 -> 2H2O.
Include units, charges, coefficients, and conditions when necessary for correctness.

For multiple questions, return one answer per line and preserve the original question numbers. Use the language of the question unless instructed otherwise.

If essential information is missing or unreadable, briefly state what is needed. Do not guess."""

DEFAULTS = {
    "hotkey": "ctrl+alt+space", "image_hotkey": "ctrl+alt+i",
    "exit_hotkey": "ctrl+alt+f12",
    "system_prompt": DEFAULT_PROMPT, "system_prompt_enabled": True,
    "show_window_on_start": True, "dot": True, "suppress_keys": True,
    "bridge_port": 8001, "request_timeout": 240,
}
MODIFIER_NAMES = ("ctrl", "alt", "shift", "win")


def parse_hotkey(value):
    parts = [part.strip().lower() for part in value.split("+")]
    if set(parts) in ({"ctrl", "alt", "del"}, {"ctrl", "alt", "delete"},
                       {"ctrl", "shift", "esc"}, {"ctrl", "shift", "escape"},
                       {"ctrl", "esc"}, {"alt", "tab"}, {"alt", "f4"}):
        raise ValueError("That shortcut is reserved by Windows. Choose another, such as Ctrl+Alt+F12.")
    if "win" in parts:
        raise ValueError("Windows-key shortcuts are not supported. Use Ctrl, Alt, and/or Shift.")
    if not all(parts) or len(parts) != len(set(parts)):
        raise ValueError("Use one key and optional, non-repeated modifiers.")
    modifiers = frozenset(part for part in parts if part in MODIFIER_NAMES)
    keys = [part for part in parts if part not in MODIFIER_NAMES]
    if len(keys) != 1:
        raise ValueError("Hotkey needs exactly one non-modifier key.")
    key = keys[0]
    if key == "space":
        vk = 0x20
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    else:
        raise ValueError(f"Unsupported hotkey: {value}")
    return modifiers, vk


def validate_keybinds(config):
    names = {"hotkey": "Text answer", "image_hotkey": "Image answer", "exit_hotkey": "Exit app"}
    parsed = {}
    for key, label in names.items():
        try:
            parsed[key] = parse_hotkey(config[key])
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"{label}: {exc}") from exc
    for index, key in enumerate(names):
        for other in list(names)[index + 1:]:
            if parsed[key] == parsed[other]:
                raise ValueError(f"{names[key]} and {names[other]} shortcuts must be different.")
    return parsed


class HotkeyLatch:
    """A chord activates once per physical key press, with exact modifiers."""
    def __init__(self):
        self.down = set()

    def release(self, vk):
        self.down.discard(vk)

    def match(self, vk, held_modifiers, bindings):
        if vk in self.down:
            return None
        self.down.add(vk)
        for name, (modifiers, key) in bindings.items():
            if vk == key and frozenset(held_modifiers) == modifiers:
                return name
        return None


def normalize_config(saved):
    config = dict(DEFAULTS)
    config.update(saved)
    if not isinstance(config.get("system_prompt"), str) or not config["system_prompt"].strip():
        config["system_prompt"] = DEFAULT_PROMPT
    # Preserve configured shortcuts or explain the error; never silently remap.
    validate_keybinds(config)
    config["bridge_port"] = int(config["bridge_port"])
    config["request_timeout"] = max(10, int(config["request_timeout"]))
    if not 1024 <= config["bridge_port"] <= 65535:
        raise ValueError("bridge_port must be between 1024 and 65535.")
    return config


def save_config(path, config):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def load_config(path):
    path = Path(path)
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    config = normalize_config(saved)
    save_config(path, config)
    return config
