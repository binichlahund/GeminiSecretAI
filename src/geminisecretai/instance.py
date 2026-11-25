"""Windows named handles scoped to this installation, without PID-based killing."""
import ctypes
from ctypes import wintypes as wt
import hashlib
from pathlib import Path
import time


class InstanceGuard:
    def __init__(self, root, role="client"):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        self.api.CreateMutexW.restype = wt.HANDLE
        self.api.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        self.api.CreateEventW.restype = wt.HANDLE
        for name in ("CloseHandle", "SetEvent", "ResetEvent"):
            getattr(self.api, name).argtypes = [wt.HANDLE]
        self.api.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        key = hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
        self.name = f"Local\\GeminiSecretAI-{key}-{role}"
        self.handle = None
        self.event = self.api.CreateEventW(None, True, False, self.name + "-restart")
        if not self.event:
            raise ctypes.WinError(ctypes.get_last_error())

    def acquire(self, restart=False):
        self.handle = self.api.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.api.CloseHandle(self.handle)
            self.handle = None
            if not restart:
                return False
            self.api.SetEvent(self.event)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                time.sleep(.1)
                if self.acquire():
                    return True
            raise RuntimeError("The previous app has not exited. Quit it before restarting.")
        self.api.ResetEvent(self.event)
        return True

    def restart_requested(self):
        return self.api.WaitForSingleObject(self.event, 0) == 0

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
        if self.event:
            self.api.CloseHandle(self.event)
            self.event = None
