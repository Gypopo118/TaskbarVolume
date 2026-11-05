#!/usr/bin/env python3
"""
End-to-end тест Taskbar Volume.

Что проверяется:
  * приложение запускается и ставит хук;
  * колесо вверх над панелью задач: 50% -> 53%;
  * появляется OSD (ищем зелёную полоску на скриншоте);
  * колесо вниз: 53% -> 50%;
  * при mute колесо вверх снимает mute;
  * колесо вне панели задач громкость не меняет;
  * второй экземпляр молча завершается (single instance);
  * (опционально) включение/выключение автозапуска через exe.

Запуск:
  python tools/e2e_test.py                 # dist\\TaskbarVolume.exe, если собран
  python tools/e2e_test.py --source        # python main.py
  python tools/e2e_test.py --exe dist\\TaskbarVolume.exe
  python tools/e2e_test.py --no-launch     # приложение уже запущено

Требует интерактивного рабочего стола (не RDP-сессия без ввода).
"""

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dpi  # noqa: E402

dpi.enable_dpi_awareness()

from PIL import ImageGrab  # noqa: E402

from taskbar_detector import TaskbarDetector  # noqa: E402
from volume_controller import VolumeController  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE = 0
MOUSEEVENTF_WHEEL = 0x0800


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


class Results:
    def __init__(self):
        self.checks = []

    def check(self, name, ok, detail=""):
        self.checks.append((name, bool(ok), detail))
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {name}"
        if detail:
            line += f" - {detail}"
        print(line, flush=True)
        return ok

    def summary(self):
        failed = [c for c in self.checks if not c[1]]
        print("")
        print(f"Итого: {len(self.checks)} проверок, провалено: {len(failed)}")
        return len(failed) == 0


def move_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)


def send_wheel(delta):
    data = wintypes.DWORD(delta & 0xFFFFFFFF)
    item = INPUT(
        type=INPUT_MOUSE,
        union=_INPUTUNION(
            mi=MOUSEINPUT(0, 0, data, MOUSEEVENTF_WHEEL, 0, None)
        ),
    )
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        raise RuntimeError(f"SendInput failed: {ctypes.get_last_error()}")
    time.sleep(0.05)


def launch(exe_path, env=None):
    if exe_path:
        command = [exe_path]
        flags = 0
    else:
        command = [sys.executable, os.path.join(ROOT, "main.py")]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    print(f"Запуск: {' '.join(command)}", flush=True)
    return subprocess.Popen(command, cwd=ROOT, creationflags=flags, env=env)


def wait_ready(volume, point, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        volume.set_volume(50, unmute=False)
        move_cursor(*point)
        send_wheel(120)
        time.sleep(0.2)
        if volume.get_volume() != 50:
            return True
        time.sleep(0.4)
    return False


def settle(volume, target=50, timeout=3.0):
    """Гасит возможные события, оставшиеся в очереди после probe-фазы."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        volume.set_volume(target, unmute=False)
        time.sleep(0.25)
        if volume.get_volume() == target:
            time.sleep(0.25)
            if volume.get_volume() == target:
                return True
    return False


def primary_screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def taskbar_empty_point(taskbar):
    """
    Точка в пустой части панели задач (между кнопками приложений и треем).
    Если встать на кнопку приложения, Windows 11 показывает всплывающий
    thumbnail, который перекрывает OSD и ломает проверку скриншотом.
    """
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    children = []

    def callback(child, lparam):
        try:
            if not user32.IsWindowVisible(child):
                return True
            rect = RECT()
            if not user32.GetWindowRect(child, ctypes.byref(rect)):
                return True
            if rect.right - rect.left < 20:
                return True
            name = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, name, 128)
            children.append((name.value, rect))
        except Exception:
            pass
        return True

    if hwnd:
        user32.EnumChildWindows(
            hwnd,
            ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback),
            0,
        )

    task_sw = next((rect for name, rect in children if name == "MSTaskSwWClass"), None)
    tray = next((rect for name, rect in children if name == "TrayNotifyWnd"), None)
    if task_sw and tray and tray.left > task_sw.right + 200:
        x = (task_sw.right + tray.left) // 2
    else:
        x = (taskbar.left + taskbar.right) // 2
    return x, (taskbar.top + taskbar.bottom) // 2


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL


def find_osd_rect():
    """RECT видимого OSD-окна Tk (верхний Toplevel приложения)."""
    found = []

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, name, 64)
            if name.value != "TkTopLevel":
                return True
            rect = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if 100 < width < 2000 and 30 < height < 400:
                    found.append(rect)
        except Exception:
            pass
        return True

    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else None


def capture_osd(cursor_x, taskbar_top, out_path):
    """Скриншот области OSD (капсула приклеена к верхнему краю панели задач)."""
    scale = dpi.dpi_scale_for_point(cursor_x, max(0, taskbar_top - 1))
    width = int(round(200 * scale))
    height = int(round(70 * scale))
    px = cursor_x - width // 2
    py = taskbar_top - height

    image = ImageGrab.grab()
    screen_w, screen_h = primary_screen_size()
    if image.size != (screen_w, screen_h) and screen_w and screen_h:
        # DPI-unaware захват - переводим координаты в пиксели картинки
        sx = image.width / float(screen_w)
        sy = image.height / float(screen_h)
    else:
        sx = sy = 1.0

    left = max(0, int((px - 12) * sx))
    top = max(0, int((py - 12) * sy))
    right = min(image.width, int((px + width + 12) * sx))
    bottom = min(image.height, int((py + height + 12) * sy))
    crop = image.crop((left, top, right, bottom))
    crop.save(out_path)

    green = 0
    for r, g, b in crop.convert("RGB").getdata():
        if abs(r - 76) <= 14 and abs(g - 194) <= 14 and abs(b - 76) <= 14:
            green += 1
    return crop, green


def read_autostart():
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as key:
            return winreg.QueryValueEx(key, "TaskbarVolume")[0]
    except FileNotFoundError:
        return None
    except OSError:
        return None


def write_autostart(value):
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, "TaskbarVolume")
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, "TaskbarVolume", 0, winreg.REG_SZ, value)


def test_autostart(exe, results):
    print("", flush=True)
    print("== Автозапуск ==", flush=True)
    if not exe:
        print("Пропущено: автозапуск проверяется только у собранного exe", flush=True)
        return
    original = read_autostart()
    try:
        subprocess.run([exe, "--enable-autostart"], cwd=ROOT, timeout=30)
        value = read_autostart()
        ok = bool(
            value
            and exe.lower() in value.lower()
            and "--autostart" in value.lower()
        )
        results.check("exe --enable-autostart прописывает Run", ok, repr(value))

        subprocess.run([exe, "--disable-autostart"], cwd=ROOT, timeout=30)
        results.check("exe --disable-autostart удаляет Run", read_autostart() is None)
    finally:
        write_autostart(original)


def main(argv=None):
    parser = argparse.ArgumentParser(description="E2E тест Taskbar Volume")
    parser.add_argument("--exe", help="путь к TaskbarVolume.exe")
    parser.add_argument("--source", action="store_true", help="запустить main.py из исходников")
    parser.add_argument("--no-launch", action="store_true", help="приложение уже запущено")
    parser.add_argument("--skip-autostart", action="store_true")
    parser.add_argument("--outdir", default=os.path.join(
        os.getenv("TEMP", "."), "TaskbarVolume", "e2e"
    ))
    args = parser.parse_args(argv)

    os.makedirs(args.outdir, exist_ok=True)
    results = Results()

    exe = None
    if not args.source:
        exe = args.exe or os.path.join(ROOT, "dist", "TaskbarVolume.exe")
        if not os.path.isfile(exe):
            print(f"Не найден {exe}, тестирую исходники", flush=True)
            exe = None

    volume = VolumeController()
    original_volume = volume.get_volume()
    original_mute = volume.get_mute()
    if original_volume is None:
        print("Нет аудиоустройства - тест невозможен", flush=True)
        return 2

    detector = TaskbarDetector(edge_px=5)
    taskbars = detector.taskbars
    if not taskbars:
        print("Панель задач не найдена - тест невозможен", flush=True)
        return 2
    taskbar = taskbars[0][2]
    point = taskbar_empty_point(taskbar)
    print(f"Панель задач: {taskbar.as_tuple()}, точка: {point}", flush=True)

    process = None
    env = os.environ.copy()
    test_config = os.path.join(args.outdir, "test_config.json")
    try:
        import json

        with open(test_config, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "osd_duration_ms": 5000,
                    "fade_duration_ms": 200,
                    "autohide_edge_px": 5,
                },
                handle,
            )
        env["TASKBAR_VOLUME_CONFIG"] = test_config
    except OSError:
        pass

    try:
        if not args.no_launch:
            process = launch(exe, env=env)
        else:
            print("Использую уже запущенный экземпляр", flush=True)

        volume.set_volume(50, unmute=False)
        volume.set_mute(False)

        ready = wait_ready(volume, point)
        results.check("приложение запустилось и реагирует на колесо", ready)
        if not ready:
            return 1

        volume.set_volume(50, unmute=False)
        volume.set_mute(False)
        settle(volume, 50)
        time.sleep(0.2)
        print(f"Стартовое значение: {volume.get_volume()}%", flush=True)

        print("", flush=True)
        print("== Колесо над панелью задач ==", flush=True)
        move_cursor(*point)
        send_wheel(120)
        time.sleep(0.3)
        got = volume.get_volume()
        print(f"  после колеса вверх: {got}%", flush=True)
        results.check("колесо вверх: 50% -> 53%", got == 53, f"получено {got}%")

        crop_path = os.path.join(args.outdir, "osd_after_wheel.png")
        crop, green = capture_osd(point[0], taskbar.top, crop_path)
        results.check(
            "OSD с зелёной полоской виден",
            green > 300,
            f"зелёных пикселей {green}, скриншот {crop_path}",
        )

        osd_rect = find_osd_rect()
        results.check(
            "OSD приклеен к верхнему краю панели задач",
            osd_rect is not None and abs(osd_rect.bottom - taskbar.top) <= 2,
            "низ OSD={}, верх панели={}".format(
                osd_rect.bottom if osd_rect else "?", taskbar.top
            ),
        )
        first_pos = (osd_rect.left, osd_rect.top) if osd_rect else None

        # Двигаем курсор вдоль панели и крутим ещё раз: громкость меняется,
        # OSD обязан остаться на месте.
        other_x = min(taskbar.right - 300, point[0] + 300)
        move_cursor(other_x, point[1])
        send_wheel(120)
        time.sleep(0.3)
        got = volume.get_volume()
        print(f"  после колеса вверх в другой точке панели: {got}%", flush=True)
        results.check(
            "колесо в другой точке панели меняет громкость",
            got == 57,
            f"получено {got}%",
        )
        osd_rect2 = find_osd_rect()
        results.check(
            "OSD остаётся на месте при движении курсора по панели",
            first_pos is not None
            and osd_rect2 is not None
            and abs(osd_rect2.left - first_pos[0]) <= 2
            and abs(osd_rect2.top - first_pos[1]) <= 2,
            "было {}, стало {}".format(
                first_pos,
                (osd_rect2.left, osd_rect2.top) if osd_rect2 else None,
            ),
        )

        send_wheel(-120)
        time.sleep(0.3)
        got = volume.get_volume()
        print(f"  после колеса вниз: {got}%", flush=True)
        results.check("колесо вниз: 57% -> 53%", got == 53, f"получено {got}%")

        print("", flush=True)
        print("== Mute ==", flush=True)
        settle(volume, 50)
        volume.set_mute(True)
        time.sleep(0.15)
        move_cursor(*point)
        send_wheel(120)
        time.sleep(0.3)
        got = volume.get_volume()
        muted = volume.get_mute()
        print(f"  громкость {got}%, mute={muted}", flush=True)
        results.check(
            "колесо вверх снимает mute",
            got == 53 and not muted,
            f"громкость {got}%, mute={muted}",
        )

        print("", flush=True)
        print("== Вне панели задач ==", flush=True)
        settle(volume, 50)
        volume.set_mute(False)
        screen_w, screen_h = primary_screen_size()
        move_cursor(screen_w // 2, int(screen_h * 0.4))
        send_wheel(120)
        time.sleep(0.3)
        got = volume.get_volume()
        print(f"  после колеса вне панели: {got}%", flush=True)
        results.check("колесо вне панели задач игнорируется", got == 50, f"получено {got}%")

        if process and exe:
            print("", flush=True)
            print("== Single instance ==", flush=True)
            started = time.time()
            second = subprocess.run(
                [exe, "--autostart"], cwd=ROOT, timeout=15, capture_output=True
            )
            elapsed = time.time() - started
            results.check(
                "второй экземпляр завершается молча",
                second.returncode == 0 and elapsed < 8,
                f"код {second.returncode}, {elapsed:.1f} c",
            )

        if not args.skip_autostart:
            test_autostart(exe, results)

        print("", flush=True)
        print("== Чистый выход ==", flush=True)
        shutdown_env = env.copy()
        shutdown_env["TASKBAR_VOLUME_EXIT_AFTER_MS"] = "3000"
        if exe:
            shutdown_command = [exe, "--no-single-instance"]
            shutdown_flags = 0
        else:
            shutdown_command = [
                sys.executable,
                os.path.join(ROOT, "main.py"),
                "--no-single-instance",
            ]
            shutdown_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started = time.time()
        try:
            completed = subprocess.run(
                shutdown_command,
                cwd=ROOT,
                env=shutdown_env,
                timeout=25,
                creationflags=shutdown_flags,
            )
            elapsed = time.time() - started
            results.check(
                "приложение завершается по внутренней команде выхода",
                completed.returncode == 0 and elapsed < 20,
                f"код {completed.returncode}, {elapsed:.1f} c",
            )
        except subprocess.TimeoutExpired:
            results.check(
                "приложение завершается по внутренней команде выхода",
                False,
                "таймаут 25 c",
            )

    finally:
        try:
            if original_volume is not None:
                volume.set_volume(original_volume, unmute=False)
            volume.set_mute(original_mute)
        except Exception:
            pass
        volume.close()
        if process:
            # PyInstaller onefile: Popen получает bootloader, а реальное
            # приложение - дочерний процесс. Убиваем всё дерево.
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=15,
                )
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    return 0 if results.summary() else 1


if __name__ == "__main__":
    sys.exit(main())
