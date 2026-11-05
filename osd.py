"""
OSD-виджет: капсула с уровнем громкости над курсором.

Виджет живёт в главном Tk-потоке и не создаёт собственный mainloop:
раньше OSD запускал Tk в фоновом потоке, и вызовы из хука мыши
обращались к Tk из чужого потока (это небезопасно и иногда вешало приложение).

Теперь все методы show()/hide() вызываются из главного потока,
а хук лишь складывает события в очередь.
"""

import ctypes
import math
import time
import tkinter as tk
from ctypes import wintypes

from dpi import dpi_scale_for_point, get_monitor_for_point

TRANSPARENT_COLOR = "#010101"

BG_COLOR = "#1F1F1F"
BORDER_COLOR = "#303030"
TRACK_COLOR = "#3A3A3A"
FILL_COLOR = "#4CC24C"
TEXT_COLOR = "#FFFFFF"
MUTED_COLOR = "#A9A9A9"

user32 = ctypes.WinDLL("user32", use_last_error=True)

GA_ROOT = 2
GWLP_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL


class OSD:
    BASE_WIDTH = 200
    BASE_HEIGHT = 70
    BASE_RADIUS = 16

    def __init__(self, root, config=None, detector=None):
        self.root = root
        self.config = config or {}
        self.detector = detector
        self.width = self.BASE_WIDTH
        self.height = self.BASE_HEIGHT
        self.scale = 1.0
        self._hide_job = None
        self._fade_job = None
        self._visible = False
        self._alpha = 0.0
        self._hwnd = None

        # OSD - отдельный Toplevel, а не главное окно: главный root остаётся
        # скрытым, а OSD можно поднять над всеми topmost-окнами.
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            self.window.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        try:
            self.window.attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass
        self.window.configure(bg=TRANSPARENT_COLOR)
        try:
            self.window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.window,
            width=self.width,
            height=self.height,
            bg=TRANSPARENT_COLOR,
            highlightthickness=0,
            borderwidth=0,
        )
        self.canvas.pack()
        self.window.update_idletasks()
        self._apply_overlay_styles()

    # --------------------------------------------------------- win32 overlay

    def _apply_overlay_styles(self):
        """WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW: не крадём фокус и Alt-Tab."""
        try:
            hwnd = user32.GetAncestor(self.window.winfo_id(), GA_ROOT)
            if not hwnd:
                return
            self._hwnd = hwnd
            style = user32.GetWindowLongPtrW(hwnd, GWLP_EXSTYLE)
            new_style = style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
            if new_style != style:
                user32.SetWindowLongPtrW(hwnd, GWLP_EXSTYLE, new_style)
        except Exception:
            self._hwnd = None

    def _raise_overlay(self):
        """Поднимает OSD на самый верх topmost-полосы, не активируя его."""
        try:
            if not self._hwnd:
                self._apply_overlay_styles()
            if not self._hwnd:
                return
            user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ API

    @property
    def visible(self):
        return self._visible

    def apply_config(self, config):
        self.config = config

    def preview(self, volume=None):
        from dpi import get_cursor_pos
        from volume_controller import VolumeController

        x, y = get_cursor_pos()
        if volume is None:
            controller = VolumeController()
            volume = controller.get_volume()
            controller.close()
        self.show(volume if volume is not None else 50, x, y)

    def show(self, volume, cursor_x, cursor_y, muted=False):
        try:
            volume = max(0, min(100, int(volume)))
        except (TypeError, ValueError):
            volume = 0

        # Позиция и размер фиксируются при появлении. Пока OSD видим,
        # колесо обновляет только содержимое - окно не следует за курсором.
        first_show = not self._visible
        if first_show:
            scale = dpi_scale_for_point(cursor_x, cursor_y)
            if abs(scale - self.scale) > 0.01:
                self.scale = scale
                self.width = max(1, int(round(self.BASE_WIDTH * scale)))
                self.height = max(1, int(round(self.BASE_HEIGHT * scale)))
                self.canvas.configure(width=self.width, height=self.height)

        self._draw(volume, muted, self.scale)
        self._cancel_timers()

        if first_show:
            pos_x, pos_y = self._position(cursor_x, cursor_y, self.width, self.height)
            self.window.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")

        try:
            self.window.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        self.window.deiconify()
        self._raise_overlay()
        self._alpha = 1.0
        self._visible = True

        duration = int(self.config.get("osd_duration_ms", 1000))
        self._hide_job = self.window.after(max(100, duration), self._start_fade)

    def hide(self):
        """Сразу спрятать (без анимации)."""
        self._cancel_timers()
        self._visible = False
        self._alpha = 0.0
        try:
            self.window.attributes("-alpha", 0.0)
            self.window.withdraw()
        except tk.TclError:
            pass

    # -------------------------------------------------------------- drawing

    def _cancel_timers(self):
        for attr in ("_hide_job", "_fade_job"):
            job = getattr(self, attr)
            if job:
                try:
                    self.window.after_cancel(job)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, attr, None)

    def _start_fade(self):
        self._hide_job = None
        self._fade_start = time.monotonic()
        duration = max(50, int(self.config.get("fade_duration_ms", 200)))
        self._fade_duration = duration
        self._fade_step()

    def _fade_step(self):
        elapsed = (time.monotonic() - self._fade_start) * 1000.0
        progress = min(1.0, elapsed / float(self._fade_duration))
        alpha = max(0.0, 1.0 - progress)
        try:
            self.window.attributes("-alpha", alpha)
        except tk.TclError:
            pass
        self._alpha = alpha
        if progress >= 1.0:
            self._visible = False
            self._fade_job = None
            try:
                self.window.withdraw()
            except tk.TclError:
                pass
            return
        self._fade_job = self.window.after(16, self._fade_step)

    @staticmethod
    def _round_rect_points(x1, y1, x2, y2, radius, steps=8):
        radius = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
        points = []
        corners = (
            (x1 + radius, y1 + radius, 180.0),
            (x2 - radius, y1 + radius, 270.0),
            (x2 - radius, y2 - radius, 0.0),
            (x1 + radius, y2 - radius, 90.0),
        )
        for cx, cy, start in corners:
            for i in range(steps + 1):
                angle = math.radians(start + 90.0 * i / steps)
                points.append(cx + radius * math.cos(angle))
                points.append(cy + radius * math.sin(angle))
        return points

    def _rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = self._round_rect_points(x1, y1, x2, y2, radius)
        return self.canvas.create_polygon(points, smooth=False, **kwargs)

    def _draw(self, volume, muted, s):
        self.canvas.delete("all")
        w = self.width
        h = self.height

        # Капсула
        self._rounded_rect(
            1 * s,
            1 * s,
            w - 1 * s,
            h - 1 * s,
            self.BASE_RADIUS * s,
            fill=BG_COLOR,
            outline=BORDER_COLOR,
            width=max(1, int(round(s))),
        )

        # Динамик
        self._draw_speaker(33 * s, 25 * s, s, muted)

        # Проценты
        text_color = MUTED_COLOR if muted else TEXT_COLOR
        self.canvas.create_text(
            w - 24 * s,
            25 * s,
            text=f"{volume}%",
            fill=text_color,
            font=("Segoe UI", -max(9, int(round(22 * s))), "bold"),
            anchor="e",
        )

        # Полоска
        bar_x1 = 24 * s
        bar_x2 = w - 24 * s
        bar_y1 = 46 * s
        bar_height = 9 * s
        bar_y2 = bar_y1 + bar_height
        radius = bar_height / 2.0
        self._rounded_rect(
            bar_x1, bar_y1, bar_x2, bar_y2, radius, fill=TRACK_COLOR, outline=""
        )

        fill_width = (bar_x2 - bar_x1) * (volume / 100.0)
        if fill_width >= 0.5:
            fill_x2 = bar_x1 + fill_width
            if fill_width < bar_height:
                self.canvas.create_rectangle(
                    bar_x1, bar_y1, fill_x2, bar_y2, fill=FILL_COLOR, outline=""
                )
            else:
                self._rounded_rect(
                    bar_x1, bar_y1, fill_x2, bar_y2, radius, fill=FILL_COLOR, outline=""
                )

    def _draw_speaker(self, cx, cy, s, muted):
        white = TEXT_COLOR if not muted else MUTED_COLOR
        width = max(1, int(round(2 * s)))
        # Корпус
        self.canvas.create_rectangle(
            cx - 9 * s,
            cy - 4 * s,
            cx - 2 * s,
            cy + 4 * s,
            fill=white,
            outline="",
        )
        # Раструб
        self.canvas.create_polygon(
            cx - 2 * s,
            cy - 4 * s,
            cx + 6 * s,
            cy - 11 * s,
            cx + 6 * s,
            cy + 11 * s,
            cx - 2 * s,
            cy + 4 * s,
            fill=white,
            outline="",
        )

        if muted:
            size = 5 * s
            ox = cx + 13 * s
            self.canvas.create_line(
                ox - size, cy - size, ox + size, cy + size, fill=white, width=width
            )
            self.canvas.create_line(
                ox - size, cy + size, ox + size, cy - size, fill=white, width=width
            )
            return

        # Звуковые волны
        self.canvas.create_arc(
            cx + 1 * s,
            cy - 8 * s,
            cx + 12 * s,
            cy + 8 * s,
            start=-55,
            extent=110,
            style=tk.ARC,
            outline=white,
            width=width,
        )
        self.canvas.create_arc(
            cx + 1 * s,
            cy - 14 * s,
            cx + 18 * s,
            cy + 14 * s,
            start=-50,
            extent=100,
            style=tk.ARC,
            outline=white,
            width=width,
        )

    def _position(self, cursor_x, cursor_y, width, height):
        """
        Позиция при появлении: OSD приклеен к верхнему краю панели задач.
        Над кнопками приложений (где Windows показывает всплывающие превью
        окон) капсула дополнительно поднимается на preview_offset_px, чтобы
        превью её не перекрывали. Над пустой частью панели и треем - вплотную.
        """
        monitor = get_monitor_for_point(cursor_x, cursor_y)
        if monitor:
            rect = monitor["rect"]
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        else:
            left, top = 0, 0
            right = self.root.winfo_screenwidth()
            bottom = self.root.winfo_screenheight()

        scale = max(1.0, getattr(self, "scale", 1.0))
        margin = int(round(8 * scale))
        monitor_height = max(1, bottom - top)

        taskbar = None
        if self.detector is not None:
            try:
                taskbar = self.detector.taskbar_at(cursor_x, cursor_y)
            except Exception:
                taskbar = None

        clearance = 0
        if self.detector is not None:
            try:
                if self.detector.is_preview_zone(cursor_x, cursor_y):
                    offset = max(0, int(self.config.get("preview_offset_px", 200)))
                    clearance = int(round(offset * scale))
            except Exception:
                clearance = 0

        if taskbar is not None:
            if taskbar.top >= top + monitor_height / 2:
                # Панель внизу: низ капсулы = верх панели минус зазор под превью.
                pos_y = int(taskbar.top - height - clearance)
            elif taskbar.bottom <= top + monitor_height / 2:
                # Панель сверху (Windows 10): капсула ниже панели и превью.
                pos_y = int(taskbar.bottom + clearance)
            else:
                # Боковая панель: чуть выше курсора.
                pos_y = int(cursor_y - height - 16 * scale)
        else:
            # Автоскрытая панель: её край - нижняя граница монитора.
            pos_y = int(bottom - height)

        pos_x = int(cursor_x - width / 2)

        min_x = left + margin
        max_x = right - width - margin
        if max_x >= min_x:
            pos_x = max(min_x, min(pos_x, max_x))
        else:
            pos_x = left

        # По вертикали не оставляем отступ от панели, но не выходим за монитор.
        min_y = top + margin
        max_y = bottom - height
        if max_y >= min_y:
            pos_y = max(min_y, min(pos_y, max_y))
        else:
            pos_y = top
        return pos_x, pos_y


_instance = None


def get_osd(root, config=None, detector=None):
    """Один экземпляр OSD на приложение (совместимость со старым API)."""
    global _instance
    if _instance is None:
        _instance = OSD(root, config, detector=detector)
    return _instance
