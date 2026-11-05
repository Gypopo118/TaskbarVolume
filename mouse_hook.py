"""
Глобальный низкоуровневый хук мыши WH_MOUSE_LL (SetWindowsHookExW).

Преимущества перед WH_MOUSE:
  * не требует прав администратора;
  * callback выполняется в потоке, установившем хук, - можно вызывать
    detector/очередь, но нельзя блокироваться надолго.

ВАЖНО: SetWindowsHookExW и цикл сообщений должны выполняться в одном потоке.
Поэтому MouseHook создаётся и запускается целиком в отдельном потоке.
"""

import ctypes
import threading
from ctypes import POINTER, cast, wintypes

from config import get_logger

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL = 14
WM_MOUSEWHEEL = 0x020A
WM_QUIT = 0x0012
WHEEL_DELTA = 120

_log = get_logger()


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    HOOKPROC,
    wintypes.HMODULE,
    wintypes.DWORD,
]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = [
    POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [POINTER(wintypes.MSG)]
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class MouseHook:
    """
    on_wheel(x, y, notches) -> bool
        True  - событие обработано, прокрутку нужно заблокировать;
        False - передать событие дальше.
    """

    def __init__(self, on_wheel):
        self.on_wheel = on_wheel
        self.handle = None
        self.error = None
        self._proc = HOOKPROC(self._hook_proc)  # держим ссылку от GC
        self._thread_id = None
        self._stopping = False
        self._accumulator = 0
        self._sign = 0

    # ------------------------------------------------------------- lifecycle

    def install(self):
        """Устанавливает хук в ТЕКУЩЕМ потоке. Возвращает True/False."""
        self._thread_id = int(kernel32.GetCurrentThreadId())
        module = kernel32.GetModuleHandleW(None)
        ctypes.set_last_error(0)
        handle = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, module, 0)
        if not handle:
            error_code = ctypes.get_last_error()
            self.error = f"SetWindowsHookExW failed (error {error_code})"
            _log.error(self.error)
            return False
        self.handle = handle
        _log.info("Mouse hook installed: %s", handle)
        return True

    def run(self):
        """Цикл сообщений. Блокируется до stop()."""
        if not self.handle:
            return False
        msg = wintypes.MSG()
        while not self._stopping:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self.uninstall()
        _log.info("Mouse hook loop finished")
        return True

    def stop(self):
        self._stopping = True
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def uninstall(self):
        handle = self.handle
        self.handle = None
        if handle:
            if user32.UnhookWindowsHookEx(handle):
                _log.info("Mouse hook uninstalled")
            else:
                _log.warning("UnhookWindowsHookEx failed: %s", ctypes.get_last_error())

    # ------------------------------------------------------------------ hook

    def _notches(self, delta):
        """Накапливает дробные дельты тачпада в целые «щелчки» колеса."""
        sign = 1 if delta > 0 else -1
        if sign != self._sign:
            self._sign = sign
            self._accumulator = 0
        self._accumulator += delta
        count = int(self._accumulator / WHEEL_DELTA)
        self._accumulator -= count * WHEEL_DELTA
        return count

    def _hook_proc(self, n_code, w_param, l_param):
        if n_code >= 0 and w_param == WM_MOUSEWHEEL:
            try:
                info = cast(l_param, POINTER(MSLLHOOKSTRUCT)).contents
                delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                if delta:
                    notches = self._notches(delta)
                    if self.on_wheel(info.pt.x, info.pt.y, notches):
                        return 1
            except Exception:
                _log.exception("Mouse hook callback failed")
        return user32.CallNextHookEx(None, n_code, w_param, l_param)


class HookThread:
    """Ставит хук и крутит message loop в отдельном потоке."""

    def __init__(self, on_wheel):
        self.hook = MouseHook(on_wheel)
        self.thread = threading.Thread(target=self._run, name="MouseHook", daemon=True)
        self.ready = threading.Event()
        self.installed = False

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        if not self.hook.install():
            self.installed = False
        else:
            self.installed = True
        self.ready.set()
        if self.installed:
            self.hook.run()

    def stop(self):
        self.hook.stop()

    def join(self, timeout=2.0):
        if self.thread.is_alive():
            self.thread.join(timeout)
