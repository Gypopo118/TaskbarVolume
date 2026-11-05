"""Тесты конфигурации: портабельность, валидация, путь лога."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "config.json")
        self._old_env = os.environ.get("TASKBAR_VOLUME_CONFIG")
        os.environ["TASKBAR_VOLUME_CONFIG"] = self.path

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("TASKBAR_VOLUME_CONFIG", None)
        else:
            os.environ["TASKBAR_VOLUME_CONFIG"] = self._old_env
        self.tempdir.cleanup()

    def test_env_override_sets_config_path(self):
        self.assertEqual(config.config_path(), os.path.abspath(self.path))

    def test_missing_config_returns_defaults(self):
        cfg = config.load_config()
        self.assertEqual(cfg["osd_duration_ms"], 1000)
        self.assertEqual(cfg["fade_duration_ms"], 200)
        self.assertEqual(cfg["autohide_edge_px"], 5)
        self.assertTrue(cfg["anchor_bottom_right"])

    def test_save_and_load_roundtrip(self):
        cfg = dict(config.DEFAULT_CONFIG)
        cfg["osd_duration_ms"] = 2500
        cfg["autohide_edge_px"] = 7
        self.assertTrue(config.save_config(cfg))
        loaded = config.load_config()
        self.assertEqual(loaded["osd_duration_ms"], 2500)
        self.assertEqual(loaded["autohide_edge_px"], 7)

    def test_values_are_clamped(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "osd_duration_ms": 999999,
                    "fade_duration_ms": -10,
                    "autohide_edge_px": 1000,
                },
                handle,
            )
        cfg = config.load_config()
        self.assertEqual(cfg["osd_duration_ms"], 10000)
        self.assertEqual(cfg["fade_duration_ms"], 50)
        self.assertEqual(cfg["autohide_edge_px"], 30)

    def test_broken_json_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        cfg = config.load_config()
        self.assertEqual(cfg["osd_duration_ms"], 1000)

    def test_anchor_bottom_right_parsing(self):
        for value, expected in (
            (False, False),
            (True, True),
            ("false", False),
            ("true", True),
            (0, False),
            (1, True),
        ):
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump({"anchor_bottom_right": value}, handle)
            self.assertIs(config.load_config()["anchor_bottom_right"], expected, value)

    def test_anchor_bottom_right_defaults_to_true(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({}, handle)
        self.assertTrue(config.load_config()["anchor_bottom_right"])

    def test_autostart_command_quotes_paths(self):
        command = config.autostart_command()
        self.assertIn("--autostart", command)
        self.assertIn('"', command)

    def test_log_path_is_absolute(self):
        self.assertTrue(os.path.isabs(config.log_path()))


if __name__ == "__main__":
    unittest.main()
