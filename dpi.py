"""
DPI awareness и геометрия мониторов.

ВАЖНО: enable_dpi_awareness() нужно вызвать до создания любых окон
(в том числе до импорта/создания tkinter), иначе координаты из WH_MOUSE_LL
(физические пиксели) не будут совпадать с координатами окон.
"""

import ctypes
from ctypes import POINTER, wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # pragma: no cover - Windows 7
    shcore = None

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

AWARENESS_UNAWARE = 0
AWARENESS_SYSTEM_AWARE = 1
AWARENESS_PER_MONITOR_AWARE = 2


def enable_dpi_awareness():
    """Включает Per-Monitor V2 (с fallback). Возвращает достигнутый уровень."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 & 0xFFFFFFFFFFFFFFFF)
        ):
            return AWARENESS_PER_MONITOR_AWARE
    except (AttributeError, OSError):
        pass

    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            shcore.SetProcessDpiAwareness.restype = ctypes.c_long
            if shcore.SetProcessDpiAwareness(AWARENESS_PER_MONITOR_AWARE) == 0:
                return AWARENESS_PER_MONITOR_AWARE
        except (AttributeError, OSError):
            pass

    try:
        user32.SetProcessDPIAware.restype = wintypes.BOOL
        if user32.SetProcessDPIAware():
            return AWARENESS_SYSTEM_AWARE
    except (AttributeError, OSError):
        pass
    return AWARENESS_UNAWARE


def get_dpi_awareness():
    """Текущий уровень DPI awareness процесса (для диагностики)."""
    try:
        user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int
        context = user32.GetThreadDpiAwarenessContext()
        return user32.GetAwarenessFromDpiAwarenessContext(context)
    except (AttributeError, OSError):
        return -1


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom

    def as_tuple(self):
        return (self.left, self.top, self.right, self.bottom)

    def __repr__(self):
        return f"RECT{self.as_tuple()}"


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


MONITORINFOF_PRIMARY = 1
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0


def _monitor_enum_proc(callback):
    return ctypes.WINFUNCTYPE(
        ctypes.c_bool, wintypes.HMONITOR, wintypes.HDC, POINTER(RECT), wintypes.LPARAM
    )(callback)


def get_monitors():
    """Список мониторов: [{'handle','rect','work','primary'}] в физических пикселях."""
    monitors = []

    def callback(hmonitor, hdc, lprc, lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            monitors.append(
                {
                    "handle": hmonitor,
                    "rect": info.rcMonitor,
                    "work": info.rcWork,
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                }
            )
        return True

    try:
        user32.EnumDisplayMonitors(None, None, _monitor_enum_proc(callback), 0)
    except Exception:
        pass
    return monitors


def get_monitor_for_point(x, y):
    """Монитор, содержащий точку (или ближайший)."""
    try:
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        point = wintypes.POINT(int(x), int(y))
        handle = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        if handle:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return {
                    "handle": handle,
                    "rect": info.rcMonitor,
                    "work": info.rcWork,
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                }
    except Exception:
        pass
    return None


def get_dpi_for_point(x, y):
    """DPI монитора, на котором находится точка (96 = 100%)."""
    monitor = get_monitor_for_point(x, y)
    if not monitor:
        return 96
    if shcore is not None:
        try:
            shcore.GetDpiForMonitor.argtypes = [
                wintypes.HMONITOR,
                ctypes.c_int,
                POINTER(wintypes.UINT),
                POINTER(wintypes.UINT),
            ]
            shcore.GetDpiForMonitor.restype = ctypes.c_long
            dpi_x = wintypes.UINT()
            dpi_y = wintypes.UINT()
            if shcore.GetDpiForMonitor(
                monitor["handle"], MDT_EFFECTIVE_DPI, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
            ) == 0:
                return int(dpi_x.value) or 96
        except Exception:
            pass
    try:
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        dc = user32.GetDC(None)
        if dc:
            gdi32 = ctypes.WinDLL("gdi32")
            gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
            gdi32.GetDeviceCaps.restype = ctypes.c_int
            dpi = gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
            user32.ReleaseDC(None, dc)
            if dpi:
                return int(dpi)
    except Exception:
        pass
    return 96


def dpi_scale_for_point(x, y):
    return max(1.0, get_dpi_for_point(x, y) / 96.0)


def get_cursor_pos():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y
