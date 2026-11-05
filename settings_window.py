"""
Окно настроек (Tk, тёмная тема в стиле OSD).

Создаётся только из главного Tk-потока. Сохраняет config.json,
переключает автозапуск и умеет показать OSD для проверки.
"""

import ctypes
import tkinter as tk
from ctypes import wintypes

from config import (
    VERSION,
    autostart_is_current,
    config_path,
    is_autostart_enabled,
    normalize_config,
    resource_path,
    set_autostart,
)
from dpi import dpi_scale_for_point, get_cursor_pos, get_monitor_for_point

BG = "#1F1F1F"
PANEL = "#262626"
TEXT = "#F2F2F2"
MUTED = "#A5A5A5"
ACCENT = "#4CC24C"
ENTRY_BG = "#303030"
BORDER = "#3A3A3A"

user32 = ctypes.WinDLL("user32", use_last_error=True)
try:
    dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
except OSError:  # pragma: no cover
    dwmapi = None

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND


def enable_dark_title_bar(window):
    """Тёмный заголовок окна (Windows 10 2004+ / Windows 11)."""
    if dwmapi is None:
        return
    try:
        hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT
        if not hwnd:
            return
        # DWMWA_USE_IMMERSIVE_DARK_MODE=20, CAPTION_COLOR=35, TEXT_COLOR=36
        for attribute, value in ((20, 1), (35, 0x001F1F1F), (36, 0x00F2F2F2)):
            data = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data)
            )
    except Exception:
        pass


class SettingsWindow:
    def __init__(self, root, config, on_save, on_preview, current_volume=None):
        self.root = root
        self.config = config
        self.on_save = on_save
        self.on_preview = on_preview
        self.current_volume = current_volume
        self.closed = False

        cursor = get_cursor_pos()
        self.scale = dpi_scale_for_point(*cursor)

        self.window = tk.Toplevel(root)
        self.window.title("Taskbar Volume - настройки")
        self.window.configure(bg=BG)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.bind("<Escape>", lambda event: self._cancel())
        self.window.bind("<Return>", lambda event: self._save())

        icon = resource_path("icon.ico")
        if icon:
            try:
                self.window.iconbitmap(icon)
            except tk.TclError:
                pass

        self._build()
        self.window.update_idletasks()
        enable_dark_title_bar(self.window)
        # DWM применяет атрибуты заголовка только после маппинга окна.
        self.window.after(60, lambda: enable_dark_title_bar(self.window))
        self._center(cursor)
        self.window.lift()
        try:
            self.window.focus_force()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ UI

    def _sp(self, value):
        return max(1, int(round(value * self.scale)))

    def _build(self):
        pad = self._sp(18)
        frame = tk.Frame(self.window, bg=BG, padx=pad, pady=pad)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=BG)
        header.pack(fill="x")
        tk.Label(
            header,
            text="Taskbar Volume",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            text=f"v{VERSION}",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).pack(side="right", pady=(self._sp(6), 0))

        tk.Label(
            frame,
            text="Громкость колесом мыши над панелью задач",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(self._sp(2), self._sp(12)))

        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(0, self._sp(12)))

        grid = tk.Frame(frame, bg=BG)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        row = 0
        self._label(grid, "Текущая громкость", row)
        volume_text = f"{self.current_volume}%" if self.current_volume is not None else "-"
        tk.Label(
            grid,
            text=volume_text,
            bg=BG,
            fg=ACCENT,
            font=("Segoe UI", 10, "bold"),
            anchor="e",
        ).grid(row=row, column=1, sticky="e", pady=self._sp(4))

        row += 1
        self._label(grid, "Показывать OSD", row)
        duration_frame = tk.Frame(grid, bg=BG)
        duration_frame.grid(row=row, column=1, sticky="e", pady=self._sp(4))
        self.duration_var = tk.StringVar(value=str(self.config.get("osd_duration_ms", 1000)))
        tk.Spinbox(
            duration_frame,
            from_=100,
            to=10000,
            increment=100,
            width=6,
            textvariable=self.duration_var,
            bg=ENTRY_BG,
            fg=TEXT,
            buttonbackground=ENTRY_BG,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        ).pack(side="left", ipady=self._sp(2))
        tk.Label(
            duration_frame, text="мс", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(side="left", padx=(self._sp(6), 0))

        row += 1
        self._label(grid, "Зона автоскрытия", row)
        edge_frame = tk.Frame(grid, bg=BG)
        edge_frame.grid(row=row, column=1, sticky="e", pady=self._sp(4))
        self.edge_var = tk.StringVar(value=str(self.config.get("autohide_edge_px", 5)))
        tk.Spinbox(
            edge_frame,
            from_=0,
            to=30,
            increment=1,
            width=6,
            textvariable=self.edge_var,
            bg=ENTRY_BG,
            fg=TEXT,
            buttonbackground=ENTRY_BG,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        ).pack(side="left", ipady=self._sp(2))
        tk.Label(
            edge_frame, text="px", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(side="left", padx=(self._sp(6), 0))

        row += 1
        self._label(grid, "Смещение над превью", row)
        preview_frame = tk.Frame(grid, bg=BG)
        preview_frame.grid(row=row, column=1, sticky="e", pady=self._sp(4))
        self.preview_offset_var = tk.StringVar(
            value=str(self.config.get("preview_offset_px", 200))
        )
        tk.Spinbox(
            preview_frame,
            from_=0,
            to=600,
            increment=10,
            width=6,
            textvariable=self.preview_offset_var,
            bg=ENTRY_BG,
            fg=TEXT,
            buttonbackground=ENTRY_BG,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
            font=("Segoe UI", 9),
        ).pack(side="left", ipady=self._sp(2))
        tk.Label(
            preview_frame, text="px", bg=BG, fg=MUTED, font=("Segoe UI", 9)
        ).pack(side="left", padx=(self._sp(6), 0))

        row += 1
        self.autostart_var = tk.BooleanVar(value=autostart_is_current())
        tk.Checkbutton(
            grid,
            text="Запускать при входе в Windows",
            variable=self.autostart_var,
            bg=BG,
            fg=TEXT,
            activebackground=BG,
            activeforeground=TEXT,
            selectcolor=ENTRY_BG,
            highlightthickness=0,
            bd=0,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(self._sp(10), self._sp(4)))

        tk.Frame(frame, bg=BORDER, height=1).pack(fill="x", pady=(self._sp(12), self._sp(10)))

        path_label = tk.Label(
            frame,
            text=f"Конфиг: {config_path()}",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=self._sp(400),
        )
        path_label.pack(fill="x", pady=(0, self._sp(12)))

        buttons = tk.Frame(frame, bg=BG)
        buttons.pack(fill="x")
        self._button(buttons, "Показать OSD", self.on_preview, primary=False).pack(side="left")
        self._button(buttons, "Сохранить", self._save, primary=True).pack(side="right")
        self._button(buttons, "Отмена", self._cancel, primary=False).pack(
            side="right", padx=(0, self._sp(8))
        )

    def _label(self, parent, text, row):
        tk.Label(
            parent,
            text=text,
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=row, column=0, sticky="w", pady=self._sp(4))

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else ENTRY_BG,
            fg="#10250F" if primary else TEXT,
            activebackground="#5FD75F" if primary else "#3A3A3A",
            activeforeground="#10250F" if primary else TEXT,
            relief="flat",
            bd=0,
            padx=self._sp(14),
            pady=self._sp(6),
            font=("Segoe UI", 9, "bold" if primary else "normal"),
            cursor="hand2",
        )

    def _center(self, cursor):
        monitor = get_monitor_for_point(*cursor)
        if not monitor:
            return
        rect = monitor["rect"]
        width = self.window.winfo_width()
        height = self.window.winfo_height()
        x = rect.left + max(0, (rect.width - width) // 2)
        y = rect.top + max(0, (rect.height - height) // 2)
        self.window.geometry(f"+{x}+{y}")

    # -------------------------------------------------------------- actions

    def _save(self):
        try:
            duration = int(float(self.duration_var.get().replace(",", ".")))
        except ValueError:
            duration = self.config.get("osd_duration_ms", 1000)
        try:
            edge = int(float(self.edge_var.get().replace(",", ".")))
        except ValueError:
            edge = self.config.get("autohide_edge_px", 5)
        try:
            preview_offset = int(float(self.preview_offset_var.get().replace(",", ".")))
        except ValueError:
            preview_offset = self.config.get("preview_offset_px", 200)

        new_config = dict(self.config)
        new_config["osd_duration_ms"] = duration
        new_config["autohide_edge_px"] = edge
        new_config["preview_offset_px"] = preview_offset
        new_config = normalize_config(new_config)

        want_autostart = bool(self.autostart_var.get())
        if want_autostart != is_autostart_enabled():
            if not set_autostart(want_autostart):
                from tkinter import messagebox

                messagebox.showerror(
                    "Taskbar Volume",
                    "Не удалось изменить автозапуск.\n"
                    "Проверьте права на HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.",
                    parent=self.window,
                )
                return

        self.on_save(new_config)
        self._close()

    def _cancel(self):
        self._close()

    def _close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.window.destroy()
        except tk.TclError:
            pass
