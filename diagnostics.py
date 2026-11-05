"""
Самодиагностика: python main.py --diagnose / TaskbarVolume.exe --diagnose.

Проверяет окружение, конфиг, автозапуск, Core Audio, определение панели
задач, установку хука, отрисовку иконки трея и создание OSD.
Отчёт печатается в консоль и сохраняется в файл (по умолчанию
%TEMP%\\TaskbarVolume\\diagnose.txt).
"""

import os
import sys
import time

import dpi
import win_utils
from config import (
    VERSION,
    autostart_command,
    autostart_is_current,
    config_path,
    is_autostart_enabled,
    is_portable_config,
    load_config,
    log_path,
    resource_path,
)
from mouse_hook import HookThread
from taskbar_detector import TaskbarDetector
from tray_icon import create_tray_image
from volume_controller import VolumeController


def _safe_print(text=""):
    try:
        print(text)
    except (OSError, UnicodeError):
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


class Report:
    def __init__(self):
        self.lines = []
        self.checks = 0
        self.failures = 0
        self.warnings = 0

    def _add(self, line):
        self.lines.append(line)
        _safe_print(line)

    def info(self, text=""):
        self._add(text)

    def section(self, title):
        self._add("")
        self._add(f"== {title} ==")

    def check(self, name, ok, detail=""):
        self.checks += 1
        if not ok:
            self.failures += 1
        mark = "OK  " if ok else "FAIL"
        line = f"[{mark}] {name}"
        if detail:
            line += f" - {detail}"
        self._add(line)
        return ok

    def warn(self, name, detail=""):
        self.warnings += 1
        line = f"[WARN] {name}"
        if detail:
            line += f" - {detail}"
        self._add(line)

    def write(self, path):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(self.lines) + "\n")
            return True
        except OSError as exc:
            _safe_print(f"Не удалось записать отчёт: {exc}")
            return False


def _check_config(report):
    report.section("Конфигурация")
    path = config_path()
    config = load_config()
    report.check("config.json читается", True, path)
    report.info(
        "  режим: {}".format("портабельный (рядом с exe)" if is_portable_config() else "%APPDATA%\\TaskbarVolume")
    )
    report.info(
        "  значения: OSD {osd_duration_ms} мс, fade {fade_duration_ms} мс, "
        "зона автоскрытия {autohide_edge_px} px".format(**config)
    )
    try:
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".taskbar-volume-write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        report.check("каталог конфига доступен для записи", True, directory)
    except OSError as exc:
        report.check("каталог конфига доступен для записи", False, str(exc))
    report.info(f"  лог: {log_path()}")
    return config


def _check_autostart(report):
    report.section("Автозапуск")
    enabled = is_autostart_enabled()
    current = autostart_is_current()
    report.info(f"  запись в Run существует: {enabled}")
    report.info(f"  указывает на текущий exe: {current}")
    report.info(f"  команда: {autostart_command()}")
    report.info(
        "  состояние: {}".format(
            "включён" if current else ("запись устарела" if enabled else "выключен")
        )
    )


def _check_audio(report):
    report.section("Аудио")
    controller = VolumeController()
    available = controller.available
    report.check("устройство вывода по умолчанию", available, controller.last_error or "")
    if not available:
        controller.close()
        return

    device_id = controller.get_device_id() or ""
    if device_id:
        report.info(f"  endpoint: {device_id}")
    volume = controller.get_volume()
    muted = controller.get_mute()
    report.check("чтение мастер-громкости", volume is not None, f"{volume}%")
    report.check(
        "установка мастер-громкости",
        bool(volume is not None and controller.set_volume(volume, unmute=False)),
        "записано текущее значение, звук не менялся",
    )
    report.info(f"  mute: {muted}")
    controller.close()


def _check_taskbar(report, config):
    report.section("Панель задач")
    detector = TaskbarDetector(edge_px=config["autohide_edge_px"])
    detector.refresh(force=True)
    taskbars = detector.taskbars
    report.check("окно панели задач найдено", bool(taskbars), f"{len(taskbars)} шт.")
    for _hwnd, cls, rect in taskbars:
        center_x = (rect.left + rect.right) // 2
        center_y = (rect.top + rect.bottom) // 2
        report.check(
            f"{cls} определяется как зона панели",
            detector.is_over_taskbar(center_x, center_y),
            f"rect={rect.as_tuple()}",
        )
    report.info("  " + detector.describe().replace("\n", "\n  "))


def _check_hook(report):
    report.section("Хук мыши WH_MOUSE_LL")
    events = []
    hook_thread = HookThread(lambda x, y, n: events.append((x, y, n)) or True)
    hook_thread.start()
    hook_thread.ready.wait(5.0)
    installed = bool(hook_thread.installed)
    report.check(
        "установка без прав администратора",
        installed,
        hook_thread.hook.error or "handle получен",
    )
    if installed:
        hook_thread.stop()
        hook_thread.join(3.0)
        report.check("корректное снятие хука", not hook_thread.thread.is_alive())


def _check_tray(report):
    report.section("Иконка трея")
    try:
        image = create_tray_image(58, muted=False)
        report.check("генерация иконки", image.size == (64, 64), f"{image.size}")
        colors = image.convert("RGB").getcolors(maxcolors=1000000) or []
        green = any(abs(r - 76) < 8 and abs(g - 194) < 8 and abs(b - 76) < 8 for _n, (r, g, b) in colors)
        report.check("зелёная заливка присутствует", green)
        muted_image = create_tray_image(0, muted=True)
        report.check("иконка mute (крестик)", muted_image.size == (64, 64))
        path = os.path.join(win_utils.temp_dir(), "tray_icon_preview.png")
        image.save(path)
        report.info(f"  превью: {path}")
    except Exception as exc:
        report.check("генерация иконки", False, str(exc))


def _check_osd(report, config):
    report.section("OSD")
    root = None
    try:
        import tkinter as tk

        from osd import OSD

        root = tk.Tk()
        root.withdraw()
        osd = OSD(root, config)
        x, y = dpi.get_cursor_pos()
        osd.show(58, x, y)
        root.update()
        report.check(
            "создание окна OSD",
            osd.visible and osd.width > 0 and osd.height > 0,
            f"{osd.width}x{osd.height} в ({x},{y})",
        )
        icon = resource_path("icon.ico")
        report.info(f"  icon.ico: {icon or 'не найден (не критично)'}")
        osd.hide()
        root.update()
    except Exception as exc:
        report.check("создание окна OSD", False, str(exc))
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def run_diagnostics(report_path=None):
    win_utils.attach_console()
    report = Report()
    report.info(f"Taskbar Volume {VERSION} - диагностика")
    report.info(f"Время: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.info(f"Python: {sys.version.split()[0]}  frozen={getattr(sys, 'frozen', False)}")
    report.info(f"Файл: {sys.executable}")
    try:
        win_ver = sys.getwindowsversion()
        report.info(
            f"Windows: {win_ver.major}.{win_ver.minor} build {win_ver.build} "
            f"platform {win_ver.platform_version}"
        )
    except Exception:
        pass

    report.section("DPI")
    awareness = dpi.get_dpi_awareness()
    report.check(
        "Per-Monitor V2 awareness",
        awareness == 2,
        f"awareness={awareness} (2 = per-monitor)",
    )
    for monitor in dpi.get_monitors():
        report.info(
            "  monitor: {} work={} primary={}".format(
                monitor["rect"].as_tuple(), monitor["work"].as_tuple(), monitor["primary"]
            )
        )

    config = _check_config(report)
    _check_autostart(report)
    _check_audio(report)
    _check_taskbar(report, config)
    _check_hook(report)
    _check_tray(report)
    _check_osd(report, config)

    report.section("Итог")
    report.info(f"Проверок: {report.checks}, ошибок: {report.failures}, предупреждений: {report.warnings}")
    if report.failures == 0:
        report.info("РЕЗУЛЬТАТ: OK - приложение готово к работе")
    else:
        report.info("РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ - см. FAIL выше")

    path = report_path or os.path.join(win_utils.temp_dir(), "diagnose.txt")
    path = os.path.abspath(path)
    if report.write(path):
        report.info(f"Отчёт сохранён: {path}")
    return 0 if report.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run_diagnostics())
