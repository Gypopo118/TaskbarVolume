"""
Volume controller - управление мастер-громкостью Windows через Core Audio API.

Раньше здесь использовался comtypes, но он:
  * добавлял лишнюю зависимость к портабельному exe;
  * ломался на свежих версиях Python;
  * а объявленные вручную интерфейсы имели неверные индексы vtable
    (comtypes строит vtable строго по порядку _methods_, поэтому
    GetMasterVolumeLevelScalar попадал в слот RegisterControlChangeNotify).

Теперь COM вызывается напрямую через ctypes: те же интерфейсы
IMMDeviceEnumerator -> IMMDevice -> IAudioEndpointVolume, но без codegen
и без зависимости от comtypes. Работает без прав администратора.

Все методы класса должны вызываться из одного потока (STA).
"""

import ctypes
import uuid
from ctypes import POINTER, byref, c_float, c_long, c_ulong, c_void_p
from ctypes import wintypes

__all__ = [
    "VolumeController",
    "VOLUME_STEPS",
    "get_next_volume",
    "S_OK",
]

ole32 = ctypes.WinDLL("ole32", use_last_error=True)

CLSCTX_ALL = 23
COINIT_APARTMENTTHREADED = 0x2

# EDataFlow / ERole
EDataFlow_eRender = 0
ERole_eConsole = 0
ERole_eMultimedia = 1

# HRESULT
S_OK = 0
S_FALSE = 1
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
IID_IMMDevice = "{D666063F-1587-4E43-81F1-B948E807363F}"
IID_IAudioEndpointVolume = "{5CDF2C82-841E-4546-9722-0CF74078229A}"


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, value=None):
        super().__init__()
        if value:
            u = uuid.UUID(value)
            self.Data1 = u.fields[0]
            self.Data2 = u.fields[1]
            self.Data3 = u.fields[2]
            for i, b in enumerate(u.bytes[8:]):
                self.Data4[i] = b


def _guid(value):
    return GUID(value)


def _method(interface, index, restype, *argtypes):
    """Возвращает вызываемый метод COM по индексу в vtable."""
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return prototype(vtable[index])


def _release(interface):
    if not interface:
        return
    try:
        _method(interface, 2, c_ulong)(interface)
    except Exception:
        pass


class VolumeController:
    """
    Мастер-громкость устройства вывода по умолчанию.

    get_volume() -> int | None       (0..100, None если нет устройства)
    set_volume(level, unmute=True)   (True при успехе)
    get_mute() -> bool
    refresh_device() -> bool         (переоткрыть endpoint, если устройство сменилось)
    close()
    """

    def __init__(self):
        self._enumerator = None
        self._device = None
        self._volume = None
        self._device_id = None
        self._co_initialized = False
        self.last_error = None
        self._open()

    # ------------------------------------------------------------------ COM

    def _co_init(self):
        if self._co_initialized:
            return True
        try:
            hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        except Exception as exc:  # pragma: no cover - зависит от окружения
            self.last_error = f"CoInitializeEx: {exc}"
            return False
        if hr in (S_OK, S_FALSE):
            self._co_initialized = True
            return True
        if hr == RPC_E_CHANGED_MODE:
            # Поток уже в MTA. Core Audio в MTA тоже работает, но
            # CoUninitialize вызывать нельзя.
            self.last_error = "COM уже инициализирован как MTA"
            return True
        self.last_error = f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08X}"
        return False

    def _open(self):
        """Создаёт enumerator и открывает endpoint по умолчанию."""
        if not self._co_init():
            return False
        try:
            if not self._enumerator:
                enumerator = c_void_p()
                hr = ole32.CoCreateInstance(
                    byref(_guid(CLSID_MMDeviceEnumerator)),
                    None,
                    CLSCTX_ALL,
                    byref(_guid(IID_IMMDeviceEnumerator)),
                    byref(enumerator),
                )
                if hr < 0:
                    self.last_error = f"CoCreateInstance(MMDeviceEnumerator) 0x{hr & 0xFFFFFFFF:08X}"
                    return False
                self._enumerator = enumerator

            device = c_void_p()
            last_hr = 0
            for role in (ERole_eMultimedia, ERole_eConsole):
                get_default = _method(
                    self._enumerator,
                    4,  # IMMDeviceEnumerator::GetDefaultAudioEndpoint
                    c_long,
                    ctypes.c_int,
                    ctypes.c_int,
                    POINTER(c_void_p),
                )
                last_hr = get_default(self._enumerator, EDataFlow_eRender, role, byref(device))
                if last_hr >= 0:
                    break
            if last_hr < 0 or not device.value:
                self.last_error = f"GetDefaultAudioEndpoint 0x{last_hr & 0xFFFFFFFF:08X}"
                return False

            volume = c_void_p()
            activate = _method(
                device,
                3,  # IMMDevice::Activate
                c_long,
                POINTER(GUID),
                ctypes.c_ulong,
                c_void_p,
                POINTER(c_void_p),
            )
            hr = activate(
                device,
                byref(_guid(IID_IAudioEndpointVolume)),
                CLSCTX_ALL,
                None,
                byref(volume),
            )
            if hr < 0 or not volume.value:
                _release(device)
                self.last_error = f"Activate(IAudioEndpointVolume) 0x{hr & 0xFFFFFFFF:08X}"
                return False

            self._device = device
            self._volume = volume
            self._device_id = self._read_device_id(device)
            self.last_error = None
            return True
        except Exception as exc:  # pragma: no cover - защита от неожиданностей
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._reset()
            return False

    def _read_device_id(self, device):
        try:
            get_id = _method(device, 5, c_long, POINTER(c_void_p))  # IMMDevice::GetId
            buf = c_void_p()
            if get_id(device, byref(buf)) < 0 or not buf.value:
                return None
            value = ctypes.wstring_at(buf.value)
            ole32.CoTaskMemFree(buf)
            return value
        except Exception:
            return None

    def _reset(self):
        _release(self._volume)
        _release(self._device)
        self._volume = None
        self._device = None
        self._device_id = None

    def _ensure(self):
        if self._volume:
            return True
        return self._open()

    def close(self):
        self._reset()
        _release(self._enumerator)
        self._enumerator = None
        if self._co_initialized:
            try:
                ole32.CoUninitialize()
            except Exception:
                pass
            self._co_initialized = False

    # ------------------------------------------------------------- volume

    @property
    def available(self):
        return self._ensure()

    def get_device_id(self):
        self._ensure()
        return self._device_id

    def get_volume(self):
        """Текущая громкость 0..100 или None, если устройства нет."""
        if not self._ensure():
            return None
        try:
            level = c_float(0.0)
            hr = _method(self._volume, 9, c_long, POINTER(c_float))(self._volume, byref(level))
            if hr < 0:
                self.last_error = f"GetMasterVolumeLevelScalar 0x{hr & 0xFFFFFFFF:08X}"
                self._reset()
                return None
            return max(0, min(100, int(round(level.value * 100))))
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._reset()
            return None

    def get_mute(self):
        if not self._ensure():
            return False
        try:
            mute = wintypes.BOOL()
            hr = _method(self._volume, 15, c_long, POINTER(wintypes.BOOL))(self._volume, byref(mute))
            if hr < 0:
                self._reset()
                return False
            return bool(mute.value)
        except Exception:
            self._reset()
            return False

    def set_mute(self, muted):
        """Включает/выключает mute (True при успехе)."""
        if not self._ensure():
            return False
        try:
            set_mute = _method(self._volume, 14, c_long, wintypes.BOOL, c_void_p)
            hr = set_mute(self._volume, wintypes.BOOL(bool(muted)), None)
            if hr < 0:
                self.last_error = f"SetMute 0x{hr & 0xFFFFFFFF:08X}"
                return False
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False

    def set_volume(self, level, unmute=True):
        """Устанавливает громкость 0..100. Возвращает True при успехе."""
        if not self._ensure():
            return False
        try:
            level = max(0, min(100, int(level)))
        except (TypeError, ValueError):
            return False
        try:
            if unmute and level > 0:
                mute = wintypes.BOOL()
                hr = _method(self._volume, 15, c_long, POINTER(wintypes.BOOL))(self._volume, byref(mute))
                if hr >= 0 and mute.value:
                    set_mute = _method(self._volume, 14, c_long, wintypes.BOOL, c_void_p)
                    if set_mute(self._volume, wintypes.BOOL(False), None) < 0:
                        return False

            set_level = _method(self._volume, 7, c_long, c_float, c_void_p)
            hr = set_level(self._volume, c_float(level / 100.0), None)
            if hr < 0:
                self.last_error = f"SetMasterVolumeLevelScalar 0x{hr & 0xFFFFFFFF:08X}"
                self._reset()
                return False
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._reset()
            return False

    def refresh_device(self):
        """Переоткрывает endpoint, если устройство вывода по умолчанию сменилось."""
        if not self._enumerator:
            return self._open()

        try:
            device = c_void_p()
            get_default = _method(
                self._enumerator, 4, c_long, ctypes.c_int, ctypes.c_int, POINTER(c_void_p)
            )
            hr = get_default(self._enumerator, EDataFlow_eRender, ERole_eMultimedia, byref(device))
            if hr < 0 or not device.value:
                _release(device)
                return False
            current_id = self._read_device_id(device)
            _release(device)
            if current_id and current_id != self._device_id:
                self._reset()
                return self._open()
            if not self._volume:
                return self._open()
            return True
        except Exception:
            return False


# Таблица шагов 3-4%: 31 шаг ровно доходит от 0 до 100.
VOLUME_STEPS = [
    0, 3, 7, 10, 13, 17, 20, 23, 27, 30, 33, 37, 40, 43, 47, 50,
    53, 57, 60, 63, 67, 70, 73, 77, 80, 83, 87, 90, 93, 97, 100,
]


def get_next_volume(current, delta):
    """Следующий шаг таблицы вверх (delta > 0) или вниз (delta < 0)."""
    current = max(0, min(100, int(current)))
    closest_idx = min(range(len(VOLUME_STEPS)), key=lambda i: abs(VOLUME_STEPS[i] - current))
    if delta > 0:
        if current < VOLUME_STEPS[closest_idx]:
            return VOLUME_STEPS[closest_idx]
        if closest_idx < len(VOLUME_STEPS) - 1:
            return VOLUME_STEPS[closest_idx + 1]
        return 100

    if current > VOLUME_STEPS[closest_idx]:
        return VOLUME_STEPS[closest_idx]
    if closest_idx > 0:
        return VOLUME_STEPS[closest_idx - 1]
    return 0
