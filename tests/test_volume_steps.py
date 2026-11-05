"""Юнит-тесты таблицы шагов громкости."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from volume_controller import VOLUME_STEPS, get_next_volume  # noqa: E402


class VolumeStepsTest(unittest.TestCase):
    def test_table_is_strictly_increasing(self):
        self.assertEqual(VOLUME_STEPS[0], 0)
        self.assertEqual(VOLUME_STEPS[-1], 100)
        for previous, current in zip(VOLUME_STEPS, VOLUME_STEPS[1:]):
            self.assertLess(previous, current)

    def test_steps_are_three_or_four_percent(self):
        for previous, current in zip(VOLUME_STEPS, VOLUME_STEPS[1:]):
            self.assertIn(current - previous, (3, 4))

    def test_up_reaches_100(self):
        volume = 0
        count = 0
        while volume < 100:
            volume = get_next_volume(volume, 1)
            count += 1
            self.assertLess(count, 100)
        self.assertEqual(volume, 100)
        self.assertEqual(count, len(VOLUME_STEPS) - 1)

    def test_down_reaches_0(self):
        volume = 100
        count = 0
        while volume > 0:
            volume = get_next_volume(volume, -1)
            count += 1
            self.assertLess(count, 100)
        self.assertEqual(volume, 0)
        self.assertEqual(count, len(VOLUME_STEPS) - 1)

    def test_round_trip(self):
        for level in VOLUME_STEPS:
            self.assertEqual(get_next_volume(level, 1), 100 if level == 100 else VOLUME_STEPS[VOLUME_STEPS.index(level) + 1])
        for level in VOLUME_STEPS:
            self.assertEqual(get_next_volume(level, -1), 0 if level == 0 else VOLUME_STEPS[VOLUME_STEPS.index(level) - 1])

    def test_off_table_values_snap_to_nearest_step(self):
        self.assertEqual(get_next_volume(51, 1), 53)
        self.assertEqual(get_next_volume(51, -1), 50)
        self.assertEqual(get_next_volume(99, 1), 100)
        self.assertEqual(get_next_volume(1, -1), 0)

    def test_limits_are_stable(self):
        self.assertEqual(get_next_volume(100, 1), 100)
        self.assertEqual(get_next_volume(0, -1), 0)

    def test_out_of_range_input_is_clamped(self):
        self.assertEqual(get_next_volume(-10, 1), 3)
        self.assertEqual(get_next_volume(150, -1), 97)


if __name__ == "__main__":
    unittest.main()
