"""Developer-only UI preview. Does not launch Gemini or register hotkeys."""
import argparse
from pathlib import Path
import tempfile
from unittest.mock import patch

from . import client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=1)
    parser.add_argument("--size", default="760x760")
    parser.add_argument("--page", choices=["Home", "Settings", "Help"], default="Home")
    parser.add_argument("--seconds", type=int, default=600)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="geminisecretai-preview-") as temporary, \
         patch.object(client, "CONFIG_PATH", str(Path(temporary) / "config.json")), \
         patch.object(client, "post", return_value={}):
        client.ctk.set_appearance_mode("dark")
        client.ctk.set_widget_scaling(args.scale)
        client.ctk.set_window_scaling(args.scale)
        root = client.ctk.CTk()
        client.dot = client.Dot(client.tk.Toplevel(root))
        with patch.object(client.Window, "poll_bridge"):
            window = client.Window(root)
        root.title("GeminiSecretAI — UI preview")
        root.geometry(args.size)
        window.bridge_status = {"phase": "ready", "headless": True, "mode": "run", "browser": True}
        window.set_message("")
        window.show_page(args.page)
        root.after(args.seconds * 1000, root.destroy)
        root.mainloop()


if __name__ == "__main__":
    main()
