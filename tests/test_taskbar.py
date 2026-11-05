"""Тесты определения панели задач (требуют Windows с explorer.exe)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dpi  # noqa: E402

dpi.enable_dpi_awareness()

from taskbar_detector import TaskbarDetector  # noqa: E402


class TaskbarDetectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.detector = TaskbarDetector(edge_px=5)
        cls.detector.refresh(force=True)

    def test_taskbar_windows_found(self):
        self.assertTrue(self.detector.taskbars, "не найдено ни одного окна панели задач")

    def test_monitors_found(self):
        self.assertTrue(self.detector.monitors)

    def test_center_of_each_taskbar_is_a_zone(self):
        for _hwnd, cls, rect in self.detector.taskbars:
            x = (rect.left + rect.right) // 2
            y = (rect.top + rect.bottom) // 2
            self.assertTrue(
                self.detector.is_over_taskbar(x, y),
                f"{cls} center ({x},{y}) не определяется как панель",
            )

    def test_desktop_center_is_not_a_taskbar(self):
        monitor = self.detector.monitors[0]["rect"]
        x = (monitor.left + monitor.right) // 2
        y = (monitor.top + monitor.bottom) // 2 - 100
        self.assertFalse(self.detector.is_over_taskbar(x, y))

    def test_cache_returns_fast(self):
        rect = self.detector.taskbars[0][2]
        x = (rect.left + rect.right) // 2
        y = (rect.top + rect.bottom) // 2
        import time

        start = time.perf_counter()
        for _ in range(100):
            self.detector.is_over_taskbar(x, y)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 0.2, f"100 проверок заняли {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
