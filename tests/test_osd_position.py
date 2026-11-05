"""Тесты геометрии OSD для всех положений панели задач."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dpi import RECT  # noqa: E402
from osd import compute_position, taskbar_edge  # noqa: E402

MONITOR = RECT(0, 0, 1920, 1080)
BOTTOM = RECT(0, 1020, 1920, 1080)
TOP = RECT(0, 0, 1920, 40)
LEFT = RECT(0, 0, 60, 1080)
RIGHT = RECT(1860, 0, 1920, 1080)


class TaskbarEdgeTest(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(taskbar_edge(MONITOR, BOTTOM), "bottom")
        self.assertEqual(taskbar_edge(MONITOR, TOP), "top")
        self.assertEqual(taskbar_edge(MONITOR, LEFT), "left")
        self.assertEqual(taskbar_edge(MONITOR, RIGHT), "right")
        self.assertIsNone(taskbar_edge(MONITOR, None))

    def test_autohide_sliver_is_still_bottom(self):
        sliver = RECT(0, 1078, 1920, 1080)
        self.assertEqual(taskbar_edge(MONITOR, sliver), "bottom")


class OSDPositionTest(unittest.TestCase):
    def pos(self, cursor, taskbar, anchor=False, scale=1.0):
        return compute_position(
            cursor[0], cursor[1], 200, 70, MONITOR, taskbar, scale, anchor
        )

    def test_bottom_flush(self):
        self.assertEqual(self.pos((960, 1050), BOTTOM), (860, 950))

    def test_bottom_pinned(self):
        self.assertEqual(self.pos((960, 1050), BOTTOM, anchor=True), (1712, 950))

    def test_top_flush(self):
        self.assertEqual(self.pos((960, 20), TOP), (860, 40))

    def test_top_pinned(self):
        self.assertEqual(self.pos((960, 20), TOP, anchor=True), (1712, 40))

    def test_left_flush(self):
        self.assertEqual(self.pos((30, 540), LEFT), (60, 505))

    def test_left_pinned(self):
        self.assertEqual(self.pos((30, 540), LEFT, anchor=True), (60, 1002))

    def test_right_flush(self):
        self.assertEqual(self.pos((1890, 540), RIGHT), (1660, 505))

    def test_right_pinned(self):
        self.assertEqual(self.pos((1890, 540), RIGHT, anchor=True), (1660, 1002))

    def test_without_taskbar(self):
        self.assertEqual(self.pos((960, 540), None), (860, 1010))
        self.assertEqual(self.pos((960, 540), None, anchor=True), (1712, 1010))

    def test_horizontal_clamp(self):
        self.assertEqual(self.pos((0, 1050), BOTTOM), (8, 950))
        self.assertEqual(self.pos((1919, 1050), BOTTOM), (1712, 950))

    def test_vertical_clamp(self):
        self.assertEqual(self.pos((30, 0), LEFT), (60, 0))
        self.assertEqual(self.pos((30, 1080), LEFT), (60, 1010))

    def test_dpi_scaling(self):
        monitor = RECT(0, 0, 3840, 2160)
        taskbar = RECT(0, 2076, 3840, 2160)
        flush = compute_position(1920, 2118, 350, 122, monitor, taskbar, 1.75, False)
        pinned = compute_position(1920, 2118, 350, 122, monitor, taskbar, 1.75, True)
        self.assertEqual(flush, (1745, 1954))
        self.assertEqual(pinned, (3476, 1954))


if __name__ == "__main__":
    unittest.main()
