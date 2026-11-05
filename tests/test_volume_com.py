"""Интеграционный тест Core Audio (нужно аудиоустройство)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from volume_controller import VolumeController  # noqa: E402


class VolumeControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = VolumeController()
        if not cls.controller.available:
            raise unittest.SkipTest(f"нет аудиоустройства: {cls.controller.last_error}")

    @classmethod
    def tearDownClass(cls):
        cls.controller.close()

    def test_read_volume(self):
        volume = self.controller.get_volume()
        self.assertIsNotNone(volume)
        self.assertGreaterEqual(volume, 0)
        self.assertLessEqual(volume, 100)

    def test_set_and_restore(self):
        original = self.controller.get_volume()
        original_mute = self.controller.get_mute()
        try:
            self.assertTrue(self.controller.set_volume(47, unmute=False))
            self.assertEqual(self.controller.get_volume(), 47)
            self.assertTrue(self.controller.set_volume(33, unmute=False))
            self.assertEqual(self.controller.get_volume(), 33)
        finally:
            self.controller.set_volume(original, unmute=False)
            if original_mute:
                self.controller.set_mute(True)
        self.assertEqual(self.controller.get_volume(), original)

    def test_mute_roundtrip(self):
        original = self.controller.get_mute()
        try:
            self.assertTrue(self.controller.set_mute(True))
            self.assertTrue(self.controller.get_mute())
            self.assertTrue(self.controller.set_mute(False))
            self.assertFalse(self.controller.get_mute())
        finally:
            self.controller.set_mute(original)

    def test_device_id(self):
        self.assertTrue(self.controller.get_device_id())


if __name__ == "__main__":
    unittest.main()
