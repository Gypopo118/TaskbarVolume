"""
Конфигурация, логирование и автозапуск.

Портабельность:
  * если рядом с exe (или main.py) есть config.json - используется он;
  * иначе %APPDATA%\\TaskbarVolume\\config.json;
  * переменная окружения TASKBAR_VOLUME_CONFIG перекрывает всё (удобно для тестов).

Автозапуск: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run,
права администратора не нужны.
"""

import json
import logging
import logging.handlers
import os
import sys
import winreg

APP_NAME = "TaskbarVolume"
APP_TITLE = "Taskbar Volume"
VERSION = "1.2.1"

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULT_CONFIG = {
    "osd_duration_ms": 1000,
    "fade_duration_ms": 200,
    "autohide_edge_px": 5,
    # Зарезервировано: "auto" = таблица 3-4%, int = равномерный шаг.
    "volume_step": "auto",
}

_log = logging.getLogger("TaskbarVolume")


# --------------------------------------------------------------------- пути


def app_dir():
    """Каталог exe (frozen) или исходников."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def config_path():
    override = os.getenv("TASKBAR_VOLUME_CONFIG")
    if override:
        return os.path.abspath(override)

    portable = os.path.join(app_dir(), "config.json")
    if os.path.isfile(portable):
        return portable
    return os.path.join(data_dir(), "config.json")


def is_portable_config():
    return config_path() == os.path.join(app_dir(), "config.json")


def log_path():
    return os.path.join(data_dir(), "TaskbarVolume.log")


def resource_path(name):
    """Путь к ресурсу, встроенному в onefile exe (--add-data)."""
    base = getattr(sys, "_MEIPASS", None) or app_dir()
    path = os.path.join(base, name)
    return path if os.path.exists(path) else None


# ------------------------------------------------------------------- logging


def setup_logging(verbose=False):
    if _log.handlers:
        return _log
    _log.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    try:
        os.makedirs(data_dir(), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=512 * 1024, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        _log.addHandler(handler)
    except OSError:
        pass

    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        _log.addHandler(stream)
    return _log


def get_logger():
    return _log


# -------------------------------------------------------------------- config


def _clamp_int(value, default, minimum, maximum):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize_config(config):
    result = dict(DEFAULT_CONFIG)
    if isinstance(config, dict):
        result.update(config)
    result["osd_duration_ms"] = _clamp_int(result.get("osd_duration_ms"), 1000, 100, 10000)
    result["fade_duration_ms"] = _clamp_int(result.get("fade_duration_ms"), 200, 50, 2000)
    result["autohide_edge_px"] = _clamp_int(result.get("autohide_edge_px"), 5, 0, 30)
    step = result.get("volume_step", "auto")
    if step != "auto":
        step = _clamp_int(step, 4, 1, 50)
    result["volume_step"] = step
    return result


def load_config():
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        config = normalize_config(data)
        _log.info("Config loaded: %s", path)
        return config
    except FileNotFoundError:
        _log.info("Config not found, using defaults: %s", path)
    except (OSError, ValueError) as exc:
        _log.warning("Config read failed (%s): %s", path, exc)
    return dict(DEFAULT_CONFIG)


def save_config(config):
    path = config_path()
    config = normalize_config(config)
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, path)
        _log.info("Config saved: %s", path)
        return True
    except OSError as exc:
        _log.error("Config save failed (%s): %s", path, exc)
        return False


# ----------------------------------------------------------------- autostart


def autostart_command():
    """Команда, которую прописываем в HKCU\\...\\Run."""
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}" --autostart'

    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    python = os.path.abspath(sys.executable)
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(pythonw):
        python = pythonw
    if not script:
        return f'"{python}" --autostart'
    return f'"{python}" "{script}" --autostart'


def get_autostart_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            try:
                value, _kind = winreg.QueryValueEx(key, APP_NAME)
                return value
            except FileNotFoundError:
                return None
    except OSError as exc:
        _log.warning("Autostart read failed: %s", exc)
        return None


def is_autostart_enabled():
    """Запись в Run существует (неважно, актуальна ли она)."""
    return get_autostart_value() is not None


def autostart_is_current():
    """Запись существует и указывает на текущий exe."""
    value = get_autostart_value()
    if not value:
        return False
    return value.strip().lower() == autostart_command().strip().lower()


def set_autostart(enabled):
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
                _log.info("Autostart enabled: %s", autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    _log.info("Autostart disabled")
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        _log.error("Autostart write failed: %s", exc)
        return False
