"""Мелкие WinAPI-хелперы: single instance, MessageBox, консоль для диагностики."""

import ctypes
import os
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ERROR_ALREADY_EXISTS = 183

MB_OK = 0x00000000
MB_ICONERROR = 0x00000010
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000

user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
user32.MessageBoxW.restype = ctypes.c_int
user32.GetForegroundWindow.restype = ctypes.c_void_p
user32.IsWindow.argtypes = [ctypes.c_void_p]
user32.IsWindow.restype = ctypes.c_bool
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.SetForegroundWindow.restype = ctypes.c_bool
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_bool
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
kernel32.AllocConsole.restype = ctypes.c_bool
kernel32.SetConsoleOutputCP.argtypes = [ctypes.c_uint]
kernel32.SetConsoleOutputCP.restype = ctypes.c_bool
kernel32.SetConsoleCP.argtypes = [ctypes.c_uint]
kernel32.SetConsoleCP.restype = ctypes.c_bool

_mutex_handle = None


def show_message(text, title="Taskbar Volume", error=False):
    """MessageBox без Tk - можно вызывать до создания окна."""
    if sys.stderr is not None:
        print(f"{title}: {text}", file=sys.stderr)
    flags = MB_OK | MB_SETFOREGROUND | (MB_ICONERROR if error else MB_ICONINFORMATION)
    try:
        return user32.MessageBoxW(None, text, title, flags)
    except Exception:
        return 0


def get_foreground_window():
    return user32.GetForegroundWindow()


def restore_foreground(hwnd):
    """Возвращает фокус окну, которое было активным до создания Tk-рута."""
    if not hwnd:
        return
    try:
        if user32.IsWindow(hwnd):
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def acquire_single_instance(name="Local\\TaskbarVolume.SingleInstance"):
    """True, если это первый экземпляр в сессии."""
    global _mutex_handle
    if _mutex_handle:
        return True
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


def release_single_instance():
    global _mutex_handle
    if _mutex_handle:
        kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None


def attach_console():
    """Для CLI/--diagnose из windowed exe: создать консоль и повесить stdout/stderr."""
    has_console = bool(kernel32.GetConsoleWindow())
    if not has_console:
        try:
            if not kernel32.AllocConsole():
                return False
        except Exception:
            return False
        # UTF-8, иначе русский отчёт в новой консоли превращается в кракозябры.
        try:
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            pass

    if sys.stdout is not None and sys.stderr is not None:
        # Не трогаем кодировку настоящей консоли (она уже настроена под её CP),
        # но для пайпа (Git Bash, перенаправление) включаем UTF-8.
        for stream in (sys.stdout, sys.stderr):
            try:
                if not stream.isatty():
                    stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
        return False

    try:
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1, errors="replace")
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
    except OSError:
        return False
    return True


def temp_dir():
    base = os.getenv("TEMP") or os.getenv("TMP") or os.path.expanduser("~")
    path = os.path.join(base, "TaskbarVolume")
    os.makedirs(path, exist_ok=True)
    return path
