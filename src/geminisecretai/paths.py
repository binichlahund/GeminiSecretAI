"""Installation-relative paths and non-destructive migration of legacy data."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import uuid


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def data(self):
        return self.root / "data"

    @property
    def config(self):
        return self.data / "config.json"

    @property
    def logs(self):
        return self.data / "logs"

    @property
    def captures(self):
        return self.data / "captures"

    @property
    def debug(self):
        return self.data / "debug"

    @property
    def profile(self):
        marker = self.data / "profile.json"
        if marker.exists():
            relative = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
            resolved = (self.root / relative).resolve()
            if not resolved.is_relative_to(self.root.resolve()):
                raise ValueError("Browser profile must be inside the installation folder.")
            return resolved
        legacy = self.root / "gemini-chrome"
        return legacy if legacy.is_dir() else self.data / "browser"

    def prepare(self):
        for folder in (self.data, self.logs, self.captures, self.debug, self.data / "legacy"):
            folder.mkdir(parents=True, exist_ok=True)
        legacy_config = self.root / "config.json"
        if legacy_config.is_file() and not self.config.exists():
            original = legacy_config.read_bytes()
            json.loads(original)  # Do not relocate an unreadable configuration.
            with self.config.open("xb") as target:
                target.write(original)
            if self.config.read_bytes() != original:
                raise RuntimeError("Settings migration verification failed; original settings were preserved.")
            archive = self._archive_path("config-original.json")
            legacy_config.rename(archive)
        # Pin the original Chrome location. Authentication data is never moved.
        marker = self.data / "profile.json"
        if not marker.exists():
            profile = self.profile
            with marker.open("x", encoding="utf-8") as target:
                json.dump({"path": str(profile.relative_to(self.root))}, target)
        for name in ("gemini_hotkey.log", "gemini_bridge.log"):
            old = self.root / name
            if old.is_file():
                destination = self.logs / name
                if destination.exists():
                    destination = self._archive_path(name)
                try:
                    old.rename(destination)
                except PermissionError:
                    # A legacy process may still own its log; leave it intact.
                    pass
        for name in ("captures", "gemini-debug"):
            old = self.root / name
            if old.is_dir():
                try:
                    shutil.move(str(old), str(self._archive_path(name)))
                except PermissionError:
                    pass
        return self

    def _archive_path(self, name):
        destination = self.data / "legacy" / name
        if destination.exists():
            destination = destination.with_name(f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}")
        return destination


PATHS = AppPaths(Path(os.getenv("GEMINISECRETAI_ROOT", Path(__file__).resolve().parents[2])).resolve())
