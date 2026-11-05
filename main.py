"""
Taskbar Volume - регулировка громкости скроллом над панелью задач.

Архитектура:
  * главный поток - Tk (OSD + окно настроек) и очередь событий;
  * поток MouseHook - WH_MOUSE_LL и message loop;
  * поток Tray - pystray.

Хук не трогает Tk и COM напрямую: он только проверяет попадание в панель
задач и кладёт событие в очередь. Громкость и OSD обновляются в главном
потоке, поэтому нет гонок между COM (STA), Tk и треем.

Запуск:
  python main.py
  python main.py --diagnose
  TaskbarVolume.exe --diagnose
"""

import argparse
import os
import queue
import sys
import time

import dpi

# DPI awareness обязательно до создания окон/Tk (координаты WH_MOUSE_LL физические).
dpi.enable_dpi_awareness()

import tkinter as tk

from config import (
    APP_TITLE,
    VERSION,
    autostart_command,
    autostart_is_current,
    config_path,
    get_logger,
    is_autostart_enabled,
    is_portable_config,
    load_config,
    resource_path,
    save_config,
    set_autostart,
    setup_logging,
)
import win_utils
from mouse_hook import HookThread
from osd import OSD
from taskbar_detector import TaskbarDetector
from tray_icon import TrayManager
from volume_controller import VolumeController, get_next_volume

MUTEX_NAME = "Local\\TaskbarVolume.SingleInstance"
TRAY_RETRIES = 3


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="TaskbarVolume",
        description="Регулировка громкости колесом мыши над панелью задач",
    )
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="запуск при входе в Windows (без диалогов об ошибке)",
    )
    parser.add_argument("--diagnose", action="store_true", help="прогнать самодиагностику")
    parser.add_argument(
        "--diagnose-report",
        metavar="PATH",
        help="куда сохранить отчёт диагностики",
    )
    parser.add_argument(
        "--enable-autostart", action="store_true", help="включить автозапуск и выйти"
    )
    parser.add_argument(
        "--disable-autostart", action="store_true", help="выключить автозапуск и выйти"
    )
    parser.add_argument(
        "--autostart-status", action="store_true", help="показать состояние автозапуска"
    )
    parser.add_argument("--version", action="store_true", help="показать версию")
    parser.add_argument(
        "--no-single-instance",
        action="store_true",
        help="не проверять, что приложение уже запущено (для тестов)",
    )
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    return parser.parse_args(argv)


def _emit(text):
    try:
        get_logger().info(text)
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            print(text)
        except (OSError, UnicodeError):
            pass


class Application:
    def __init__(self, args):
        self.args = args
        self.log = get_logger()
        self.config = load_config()
        self.events = queue.Queue()

        # tk.Tk() на мгновение забирает фокус даже при withdraw() - вернём его.
        previous_foreground = win_utils.get_foreground_window()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_TITLE)
        icon = resource_path("icon.ico")
        if icon:
            try:
                self.root.iconbitmap(icon)
            except tk.TclError:
                pass

        self.volume = VolumeController()
        self.detector = TaskbarDetector(edge_px=self.config["autohide_edge_px"])
        self.osd = OSD(self.root, self.config, detector=self.detector)
        self.tray = TrayManager(
            post=self.events.put,
            autostart_current=autostart_is_current,
        )
        self.settings_window = None
        self.hook_thread = None
        self.hook_ready = False
        self.started = False
        self.quitting = False
        self.tray_attempts = 0
        self.menu_job = None
        self.last_volume = None
        self.last_muted = False

        win_utils.restore_foreground(previous_foreground)

    # ------------------------------------------------------------- lifecycle

    def run(self):
        self.log.info("Config: %s (portable=%s)", config_path(), is_portable_config())
        self._refresh_volume(initial=True)

        self.root.after(10, self._pump)
        self.root.after(2000, self._poll)
        if self.args.autostart:
            self.log.info("Autostart mode: delaying services by 1.5s")
            self.root.after(1500, self._start_services)
        else:
            self._start_services()

        # Тестовый хук: позволяет проверить чистый выход exe.
        exit_after = os.getenv("TASKBAR_VOLUME_EXIT_AFTER_MS")
        if exit_after:
            try:
                self.root.after(max(500, int(exit_after)), self._quit)
                self.log.info("Auto-quit after %s ms (test hook)", exit_after)
            except ValueError:
                pass

        try:
            self.root.mainloop()
        finally:
            self._shutdown()
        return 0

    def _start_services(self):
        if self.started or self.quitting:
            return
        self.started = True
        volume = self.last_volume if self.last_volume is not None else 50
        muted = bool(self.last_muted)
        self.tray.start(volume, muted)
        self.hook_thread = HookThread(self._on_wheel).start()
        deadline = time.monotonic() + 5.0
        self.root.after(100, lambda: self._check_hook_ready(deadline))

    def _check_hook_ready(self, deadline):
        if self.quitting:
            return
        if self.hook_thread.ready.is_set():
            if self.hook_thread.installed:
                self.hook_ready = True
                self.log.info("Hook is ready")
            else:
                self._fatal(
                    "Не удалось установить глобальный хук мыши.\n"
                    f"{self.hook_thread.hook.error or 'неизвестная ошибка'}\n\n"
                    "Приложение не сможет регулировать громкость."
                )
            return
        if time.monotonic() > deadline:
            self._fatal("Хук мыши не установился за 5 секунд.")
            return
        self.root.after(100, lambda: self._check_hook_ready(deadline))

    def _shutdown(self):
        self.log.info("Shutting down")
        try:
            self.osd.hide()
        except Exception:
            pass
        if self.hook_thread:
            self.hook_thread.stop()
            self.hook_thread.join(2.0)
        try:
            self.tray.stop()
            self.tray.join(2.0)
        except Exception:
            pass
        try:
            self.volume.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        self.log.info("Exited")

    def _quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.log.info("Quit requested")
        try:
            self.root.quit()
        except tk.TclError:
            pass

    def _fatal(self, message):
        self.log.error(message)
        if not self.args.autostart:
            win_utils.show_message(message, error=True)
        self._quit()

    # --------------------------------------------------------------- events

    def _pump(self):
        if self.quitting:
            return
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception:
                self.log.exception("Event handling failed: %r", event)
        self.root.after(20, self._pump)

    def _handle_event(self, event):
        kind = event[0]
        if kind == "wheel":
            self._change_volume(event[1], event[2], event[3])
        elif kind == "settings":
            self._open_settings()
        elif kind == "toggle_autostart":
            self._toggle_autostart()
        elif kind == "tray_failed":
            self._handle_tray_failure(event[1])
        elif kind == "quit":
            self._quit()

    # ---------------------------------------------------------------- hook

    def _on_wheel(self, x, y, notches):
        """Вызывается из потока хука. Должен быть быстрым."""
        try:
            over_taskbar = self.detector.is_over_taskbar(x, y)
        except Exception:
            self.log.exception("Taskbar detection failed")
            return False
        if not over_taskbar:
            return False
        if notches:
            self.events.put(("wheel", x, y, notches))
        return True

    # -------------------------------------------------------------- volume

    def _change_volume(self, x, y, notches):
        current = self.volume.get_volume()
        if current is None:
            self.log.warning("No audio endpoint, wheel ignored")
            return

        target = current
        direction = 1 if notches > 0 else -1
        for _ in range(abs(notches)):
            next_level = get_next_volume(target, direction)
            if next_level == target:
                break
            target = next_level

        if not self.volume.set_volume(target, unmute=(notches > 0)):
            self.log.warning("Volume set failed: %s", self.volume.last_error)

        level = self.volume.get_volume()
        if level is None:
            level = target
        muted = self.volume.get_mute()
        self.log.debug("Volume %s%% -> %s%% (steps=%s, muted=%s)", current, level, notches, muted)

        self.osd.show(level, x, y, muted=muted)
        self._update_icon(level, muted)

    def _refresh_volume(self, initial=False):
        volume = self.volume.get_volume()
        muted = self.volume.get_mute()
        if volume is None:
            self.log.warning("No audio endpoint: %s", self.volume.last_error)
            return
        self.last_volume = volume
        self.last_muted = muted
        if not initial:
            self._update_icon(volume, muted)

    def _update_icon(self, volume, muted, rebuild_menu=True):
        changed = (volume, muted) != (self.last_volume, self.last_muted)
        self.last_volume = volume
        self.last_muted = muted
        try:
            self.tray.update(volume, muted)
        except Exception:
            self.log.exception("Tray update failed")
        if rebuild_menu and changed:
            self._schedule_menu_refresh()

    def _schedule_menu_refresh(self):
        if self.menu_job:
            try:
                self.root.after_cancel(self.menu_job)
            except (tk.TclError, ValueError):
                pass
        self.menu_job = self.root.after(350, self._refresh_menu)

    def _refresh_menu(self):
        self.menu_job = None
        self.tray.update_menu()

    def _poll(self):
        if self.quitting:
            return
        self.volume.refresh_device()
        volume = self.volume.get_volume()
        if volume is not None:
            self._update_icon(volume, self.volume.get_mute(), rebuild_menu=False)
        self.root.after(2000, self._poll)

    # ------------------------------------------------------------- settings

    def _open_settings(self):
        if self.settings_window and not self.settings_window.closed:
            try:
                self.settings_window.window.deiconify()
                self.settings_window.window.lift()
                self.settings_window.window.focus_force()
            except tk.TclError:
                self.settings_window = None
            return

        from settings_window import SettingsWindow

        self.settings_window = SettingsWindow(
            self.root,
            self.config,
            on_save=self._save_settings,
            on_preview=self._preview_osd,
            current_volume=self.last_volume,
        )

    def _save_settings(self, config):
        self.config = config
        save_config(config)
        self.osd.apply_config(config)
        if self.detector.edge_px != config["autohide_edge_px"]:
            self.detector.edge_px = config["autohide_edge_px"]
            self.detector.refresh(force=True)
        self.tray.update_menu()

    def _preview_osd(self):
        x, y = dpi.get_cursor_pos()
        volume = self.volume.get_volume()
        self.osd.show(volume if volume is not None else 0, x, y, muted=self.volume.get_mute())

    def _toggle_autostart(self):
        want = not autostart_is_current()
        if not set_autostart(want):
            win_utils.show_message(
                "Не удалось изменить автозапуск.\n"
                "Проверьте доступ к HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.",
                error=True,
            )
        self.tray.update_menu()
        if self.settings_window and not self.settings_window.closed:
            try:
                self.settings_window.autostart_var.set(autostart_is_current())
            except tk.TclError:
                pass

    def _handle_tray_failure(self, message):
        self.tray_attempts += 1
        self.log.warning("Tray attempt %s failed: %s", self.tray_attempts, message)
        if self.tray_attempts >= TRAY_RETRIES:
            self._fatal(
                "Не удалось создать иконку в системном трее.\n"
                f"{message}\n\n"
                "Проверьте, что проводник Windows (explorer.exe) запущен."
            )
            return
        self.root.after(3000, self._restart_tray)

    def _restart_tray(self):
        if self.quitting:
            return
        self.tray = TrayManager(
            post=self.events.put,
            autostart_current=autostart_is_current,
        )
        volume = self.last_volume if self.last_volume is not None else 50
        self.tray.start(volume, bool(self.last_muted))


def main(argv=None):
    args = parse_args(argv)
    setup_logging(args.verbose)
    log = get_logger()
    log.info("=== %s %s ===", APP_TITLE, VERSION)

    # CLI-команды в windowed exe должны быть видны: создаём консоль.
    if args.version or args.enable_autostart or args.disable_autostart or args.autostart_status:
        win_utils.attach_console()

    if args.version:
        _emit(f"{APP_TITLE} {VERSION}")
        return 0

    if args.enable_autostart or args.disable_autostart:
        enabled = bool(args.enable_autostart)
        ok = set_autostart(enabled)
        state = "включён" if enabled else "выключен"
        _emit(f"Автозапуск {state}: {'OK' if ok else 'ОШИБКА'}")
        _emit(f"Команда: {autostart_command()}")
        return 0 if ok else 1

    if args.autostart_status:
        _emit(f"Автозапуск включён: {is_autostart_enabled()}")
        _emit(f"Указывает на текущий exe: {autostart_is_current()}")
        _emit(f"Команда: {autostart_command()}")
        value = None
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            ) as key:
                value, _ = winreg.QueryValueEx(key, "TaskbarVolume")
        except OSError:
            pass
        _emit(f"Значение в реестре: {value!r}")
        return 0

    if args.diagnose:
        from diagnostics import run_diagnostics

        return run_diagnostics(args.diagnose_report)

    if not args.no_single_instance and not win_utils.acquire_single_instance(MUTEX_NAME):
        log.info("Another instance is already running")
        if not args.autostart:
            win_utils.show_message(
                "Taskbar Volume уже запущен.\n"
                "Иконка приложения находится в системном трее.",
            )
        return 0

    try:
        app = Application(args)
        return app.run()
    except Exception as exc:
        log.exception("Fatal error")
        if not args.autostart:
            win_utils.show_message(f"Критическая ошибка:\n{exc}", error=True)
        return 1
    finally:
        win_utils.release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
