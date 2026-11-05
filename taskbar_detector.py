"""
Детектор панели задач Windows 10/11.

Поддерживает:
  * основную панель (Shell_TrayWnd) и панели на дополнительных мониторах
    (Shell_SecondaryTrayWnd);
  * автоскрытие (полоса у края экрана + ABM_GETAUTOHIDEBAREX);
  * корректные физические координаты при 100/125/150/175% DPI
    (при условии, что dpi.enable_dpi_awareness() вызван до создания окон).

Результаты кешируются на доли секунды: функция вызывается из
низкоуровневого хука мыши, где нельзя тратить миллисекунды.
"""

import ctypes
import threading
import time
from ctypes import POINTER, byref, wintypes

from dpi import RECT, get_monitors

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

# AppBar messages
ABM_GETSTATE = 0x00000004
ABM_GETAUTOHIDEBAREX = 0x0000000B

# AppBar edges
ABE_LEFT = 0
ABE_TOP = 1
ABE_RIGHT = 2
ABE_BOTTOM = 3
_EDGES = (ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM)

ABS_AUTOHIDE = 0x00000001

_TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.c_void_p]
shell32.SHAppBarMessage.restype = ctypes.c_ssize_t


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", RECT),
        ("lParam", wintypes.LPARAM),
    ]


def _window_rect(hwnd):
    rect = RECT()
    if user32.GetWindowRect(hwnd, byref(rect)):
        return rect
    return None


def _child_rects(hwnd, target_class):
    """RECT'ы дочерних окон указанного класса (рекурсивно)."""
    found = []

    def callback(child, lparam):
        try:
            buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, buffer, 128)
            if buffer.value == target_class:
                rect = _window_rect(child)
                if rect and rect.width > 2 and rect.height > 2:
                    found.append(rect)
        except Exception:
            pass
        return True

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
    try:
        user32.EnumChildWindows(hwnd, enum_proc, 0)
    except Exception:
        pass
    return found


def _preview_zone_for_taskbar(hwnd, taskbar_rect):
    """
    Область панели задач, над которой Windows показывает превью окон:
    от левого края кнопок приложений (MSTaskSwWClass) до трея
    (TrayNotifyWnd). Превью появляются только над кнопками.
    """
    task_sw = _child_rects(hwnd, "MSTaskSwWClass")
    tray = _child_rects(hwnd, "TrayNotifyWnd")
    if not task_sw:
        return None
    left = min(rect.left for rect in task_sw)
    right = min(rect.left for rect in tray) if tray else taskbar_rect.right
    left = max(left, taskbar_rect.left)
    right = min(right, taskbar_rect.right)
    if right <= left + 40:
        return None
    return RECT(left, taskbar_rect.top, right, taskbar_rect.bottom)


def _enum_taskbar_windows():
    """Возвращает [(hwnd, class_name, rect)] для всех видимых панелей задач."""
    result = []
    seen = set()

    primary = user32.FindWindowW("Shell_TrayWnd", None)
    if primary:
        seen.add(int(primary))
        rect = _window_rect(primary)
        if rect and user32.IsWindowVisible(primary):
            result.append((primary, "Shell_TrayWnd", rect))

    def callback(hwnd, lparam):
        try:
            key = int(hwnd) if hwnd else 0
            if not key or key in seen:
                return True
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buffer, 256)
            if buffer.value in _TASKBAR_CLASSES and user32.IsWindowVisible(hwnd):
                rect = _window_rect(hwnd)
                if rect:
                    seen.add(key)
                    result.append((hwnd, buffer.value, rect))
        except Exception:
            pass
        return True

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
    try:
        user32.EnumWindows(enum_proc, 0)
    except Exception:
        pass
    return result


def _edge_band(monitor_rect, edge, thickness):
    if edge == ABE_BOTTOM:
        return RECT(monitor_rect.left, monitor_rect.bottom - thickness, monitor_rect.right, monitor_rect.bottom)
    if edge == ABE_TOP:
        return RECT(monitor_rect.left, monitor_rect.top, monitor_rect.right, monitor_rect.top + thickness)
    if edge == ABE_LEFT:
        return RECT(monitor_rect.left, monitor_rect.top, monitor_rect.left + thickness, monitor_rect.bottom)
    return RECT(monitor_rect.right - thickness, monitor_rect.top, monitor_rect.right, monitor_rect.bottom)


def _autohide_edges(monitor_rect):
    """Края монитора, на которых включён автоскрытый appbar."""
    edges = set()
    for edge in _EDGES:
        data = APPBARDATA()
        data.cbSize = ctypes.sizeof(APPBARDATA)
        data.uEdge = edge
        data.rc = RECT(monitor_rect.left, monitor_rect.top, monitor_rect.right, monitor_rect.bottom)
        try:
            if shell32.SHAppBarMessage(ABM_GETAUTOHIDEBAREX, byref(data)):
                edges.add(edge)
        except Exception:
            pass
    return edges


def _global_autohide():
    data = APPBARDATA()
    data.cbSize = ctypes.sizeof(APPBARDATA)
    try:
        return bool(shell32.SHAppBarMessage(ABM_GETSTATE, byref(data)) & ABS_AUTOHIDE)
    except Exception:
        return False


class TaskbarDetector:
    def __init__(self, edge_px=5, cache_seconds=0.35):
        self.edge_px = max(0, int(edge_px))
        self.cache_seconds = cache_seconds
        self._lock = threading.RLock()
        self._zones = []
        self._taskbars = []
        self._monitors = []
        self._preview_zones = []
        self._updated_at = 0.0
        self._global_autohide = False

    # ---------------------------------------------------------------- cache

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and (now - self._updated_at) < self.cache_seconds:
            return
        with self._lock:
            now = time.monotonic()
            if not force and (now - self._updated_at) < self.cache_seconds:
                return

            taskbars = _enum_taskbar_windows()
            monitors = get_monitors()
            global_autohide = _global_autohide()

            zones = [rect for (_hwnd, _cls, rect) in taskbars]
            for monitor in monitors:
                rect = monitor["rect"]
                work = monitor["work"]
                edges = set()

                if self.edge_px > 0:
                    edges |= _autohide_edges(rect)

                    # Видимый appbar (панель задач) занимает часть рабочей области.
                    if work.bottom <= rect.bottom - 2:
                        edges.add(ABE_BOTTOM)
                    if work.top >= rect.top + 2:
                        edges.add(ABE_TOP)
                    if work.left >= rect.left + 2:
                        edges.add(ABE_LEFT)
                    if work.right <= rect.right - 2:
                        edges.add(ABE_RIGHT)

                    # Автоскрытие: рабочая область совпадает с монитором,
                    # панель спрятана. ABM_GETSTATE даёт глобальный флаг.
                    if global_autohide and work.bottom == rect.bottom:
                        edges.add(ABE_BOTTOM)

                    for edge in edges:
                        zones.append(_edge_band(rect, edge, self.edge_px))

            self._taskbars = taskbars
            self._monitors = monitors
            self._global_autohide = global_autohide
            self._zones = zones
            self._preview_zones = [
                zone
                for zone in (
                    _preview_zone_for_taskbar(hwnd, rect)
                    for hwnd, _cls, rect in taskbars
                )
                if zone is not None
            ]
            self._updated_at = now

    def is_over_taskbar(self, x, y):
        self.refresh()
        with self._lock:
            for rect in self._zones:
                if rect.contains(x, y):
                    return True
        return False

    def is_preview_zone(self, x, y):
        """True, если точка над кнопками приложений, где всплывают превью."""
        self.refresh()
        with self._lock:
            for rect in self._preview_zones:
                if rect.contains(x, y):
                    return True
        return False

    def taskbar_at(self, x, y):
        """RECT окна панели задач под точкой (или None)."""
        self.refresh()
        with self._lock:
            for _hwnd, _cls, rect in self._taskbars:
                if rect.contains(x, y):
                    return rect
        return None

    # --------------------------------------------------------------- отчеты

    @property
    def taskbars(self):
        self.refresh()
        with self._lock:
            return list(self._taskbars)

    @property
    def monitors(self):
        self.refresh()
        with self._lock:
            return list(self._monitors)

    @property
    def zones(self):
        self.refresh()
        with self._lock:
            return list(self._zones)

    @property
    def preview_zones(self):
        self.refresh()
        with self._lock:
            return list(self._preview_zones)

    def describe(self):
        self.refresh(force=True)
        with self._lock:
            lines = [f"taskbar windows: {len(self._taskbars)}"]
            for _hwnd, cls, rect in self._taskbars:
                lines.append(f"  {cls}: {rect.as_tuple()}")
            lines.append(f"monitors: {len(self._monitors)}")
            for monitor in self._monitors:
                lines.append(
                    "  monitor: {} work={} primary={}".format(
                        monitor["rect"].as_tuple(),
                        monitor["work"].as_tuple(),
                        monitor["primary"],
                    )
                )
            lines.append(f"autohide (global): {self._global_autohide}")
            lines.append(f"zones: {len(self._zones)}")
            for rect in self._zones:
                lines.append(f"  {rect.as_tuple()}")
            lines.append(f"preview zones: {len(self._preview_zones)}")
            for rect in self._preview_zones:
                lines.append(f"  {rect.as_tuple()}")
            return "\n".join(lines)


_default_detector = None
_default_lock = threading.Lock()


def get_detector(edge_px=5):
    global _default_detector
    with _default_lock:
        if _default_detector is None:
            _default_detector = TaskbarDetector(edge_px=edge_px)
        return _default_detector


def is_over_taskbar(x, y):
    return get_detector().is_over_taskbar(x, y)
