"""Launcher with a useful first-install error; independent of working directory."""
import ctypes
import sys

try:
    from geminisecretai.client import run
except ModuleNotFoundError as exc:
    ctypes.windll.user32.MessageBoxW(
        None, "Installation is incomplete. Run Install.bat in the GeminiSecretAI folder first.\n\n" + str(exc),
        "GeminiSecretAI", 0x10,
    )
    raise SystemExit(1)

if __name__ == "__main__":
    run()
