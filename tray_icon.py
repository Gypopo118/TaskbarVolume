"""
Иконка в системном трее.

Иконка рисуется через Pillow: тёмный скруглённый квадрат, зелёная заливка
снизу вверх пропорционально громкости. При 0% / mute - белый крестик.

Меню pystray живёт в отдельном потоке; все действия пунктов меню не трогают
Tk напрямую, а кладут события в очередь приложения.
"""

import threading

import pystray
from PIL import Image, ImageDraw

from config import get_logger

BG = (43, 43, 43, 255)          # #2B2B2B
BORDER = (64, 64, 64, 255)
GREEN = (76, 194, 76, 255)      # #4CC24C
WHITE = (235, 235, 235, 255)

_log = get_logger()


def create_tray_image(volume, muted=False, size=64):
    """RGBA-картинка иконки трея."""
    size = max(16, int(size))
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(2, int(round(size * 0.22)))
    inset = max(1, size // 32)

    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=radius,
        fill=BG,
        outline=BORDER,
        width=inset,
    )

    try:
        volume = max(0, min(100, int(volume)))
    except (TypeError, ValueError):
        volume = 0

    if muted or volume == 0:
        padding = int(size * 0.30)
        width = max(2, int(size * 0.10))
        draw.line(
            [padding, padding, size - padding, size - padding], fill=WHITE, width=width
        )
        draw.line(
            [size - padding, padding, padding, size - padding], fill=WHITE, width=width
        )
        return image

    inner = size - 2 * inset
    fill_height = max(0, int(round(inner * volume / 100.0)))
    if fill_height > 0:
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle(
            [inset, size - inset - fill_height, size - 1 - inset, size - 1 - inset],
            fill=GREEN,
        )
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [inset, inset, size - 1 - inset, size - 1 - inset],
            radius=max(1, radius - inset),
            fill=255,
        )
        image.paste(layer, (0, 0), mask)

    # Контур поверх заливки
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=radius,
        outline=BORDER,
        width=inset,
    )
    return image


class TrayManager:
    """
    post(event: tuple) - потокобезопасная очередь событий приложения.
    События: ("settings",), ("toggle_autostart",), ("quit",), ("tray_failed", msg)
    """

    def __init__(self, post, autostart_current):
        self._post = post
        self._autostart_current = autostart_current
        self._icon = None
        self._thread = None
        self._volume = 50
        self._muted = False
        self._last_image = None
        self._last_image_key = None

    # ------------------------------------------------------------------ API

    def start(self, volume=50, muted=False):
        self._volume = volume
        self._muted = muted
        self._thread = threading.Thread(target=self._run, name="Tray", daemon=True)
        self._thread.start()

    def update(self, volume, muted):
        self._volume = volume
        self._muted = muted
        if not self._icon:
            return
        key = (int(volume), bool(muted))
        try:
            if key != self._last_image_key:
                self._icon.icon = create_tray_image(volume, muted)
                self._last_image_key = key
            self._icon.title = self._title()
        except Exception as exc:  # pragma: no cover - зависит от shell
            _log.warning("Tray update failed: %s", exc)

    def update_menu(self):
        if not self._icon:
            return
        try:
            self._icon.update_menu()
        except Exception as exc:  # pragma: no cover
            _log.warning("Tray menu update failed: %s", exc)

    def stop(self):
        icon = self._icon
        if icon:
            try:
                icon.stop()
            except Exception:
                pass

    def join(self, timeout=2.0):
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    # -------------------------------------------------------------- внутреннее

    def _title(self):
        if self._muted:
            return f"{'Taskbar Volume'} - звук выключен"
        return f"Taskbar Volume - {self._volume}%"

    def _volume_text(self):
        if self._muted:
            return "Звук выключен"
        return f"Громкость: {self._volume}%"

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._volume_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Автозапуск при входе",
                lambda icon, item: self._post(("toggle_autostart",)),
                checked=lambda item: bool(self._autostart_current()),
            ),
            pystray.MenuItem(
                "Настройки...",
                lambda icon, item: self._post(("settings",)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda icon, item: self._post(("quit",))),
        )

    def _run(self):
        try:
            self._icon = pystray.Icon(
                "TaskbarVolume",
                create_tray_image(self._volume, self._muted),
                self._title(),
                self._menu(),
            )
            self._last_image_key = (int(self._volume), bool(self._muted))
            _log.info("Tray icon starting")
            self._icon.run()
            _log.info("Tray icon stopped")
        except Exception as exc:  # pragma: no cover - зависит от shell
            _log.exception("Tray failed: %s", exc)
            try:
                self._post(("tray_failed", str(exc)))
            except Exception:
                pass
