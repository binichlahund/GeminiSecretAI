"""Physical-pixel placement for Windows capture overlays and status indicators."""
import ctypes
from ctypes import wintypes as wt


def enable_dpi_awareness():
    """Call before importing UI libraries or creating any HWNDs."""
    api = ctypes.windll.user32
    try:
        api.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        api.SetProcessDpiAwarenessContext.restype = wt.BOOL
        api.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # Per-monitor v2
        # pythonw may already have a DPI mode from its manifest.
        api.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        api.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        if api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except AttributeError:
        api.SetProcessDPIAware()


class MonitorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


class DisplayGeometry:
    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        self.api.GetAncestor.restype = wt.HWND
        self.api.GetForegroundWindow.restype = wt.HWND
        self.api.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        self.api.MonitorFromWindow.restype = wt.HANDLE
        self.api.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MonitorInfo)]
        self.api.GetMonitorInfoW.restype = wt.BOOL
        self.api.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        self.api.GetCursorPos.restype = wt.BOOL
        self.api.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wt.UINT,
        ]
        self.api.SetWindowPos.restype = wt.BOOL

    def desktop_bounds(self):
        """Bounding box of all monitors, including negative screen origins."""
        x, y, width, height = (self.api.GetSystemMetrics(i) for i in (76, 77, 78, 79))
        return x, y, x + width, y + height

    def active_work_area(self):
        monitor = self.api.MonitorFromWindow(self.api.GetForegroundWindow(), 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self.api.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        rect = info.rcWork
        return rect.left, rect.top, rect.right, rect.bottom

    def cursor_position(self):
        point = wt.POINT()
        if not self.api.GetCursorPos(ctypes.byref(point)):
            raise ctypes.WinError(ctypes.get_last_error())
        return point.x, point.y

    def place(self, window, bounds, no_activate=False):
        """Bypass Tk's cached/scaled screen geometry for a borderless window."""
        left, top, right, bottom = bounds
        window.update_idletasks()
        hwnd = self.api.GetAncestor(window.winfo_id(), 2)  # GA_ROOT, Tk wrapper
        flags = 0x0040 | (0x0010 if no_activate else 0)  # SHOWWINDOW / NOACTIVATE
        if not self.api.SetWindowPos(hwnd, wt.HWND(-1), left, top,
                                     right - left, bottom - top, flags):
            raise ctypes.WinError(ctypes.get_last_error())


def indicator_bounds(work_area, size=16, margin=18):
    left, top, right, bottom = work_area
    x, y = max(left, right - size - margin), max(top, bottom - size - margin)
    return x, y, x + size, y + size


def selection_bounds(start, end, desktop):
    """Normalize drag direction and clamp to the physical desktop edges."""
    left, top, right, bottom = desktop
    x1, x2 = sorted((start[0], end[0]))
    y1, y2 = sorted((start[1], end[1]))
    return max(left, x1), max(top, y1), min(right, x2), min(bottom, y2)
