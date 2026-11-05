"""
Taskbar Volume - устаревший all-in-one файл.

Раньше здесь лежала копия приложения на comtypes. У неё были неверные
индексы COM-vtable и она не запускалась на актуальных версиях Python,
поэтому поддерживается только основной вариант:

  * dist\\TaskbarVolume.exe  - портабельный exe (рекомендуется);
  * python main.py           - запуск из исходников.

Этот файл оставлен как ярлык для запуска из исходников.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main.main())
