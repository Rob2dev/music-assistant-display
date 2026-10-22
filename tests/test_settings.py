"""Settings storage: validation, saving, and the order of precedence (defaults < config.json < settings.json)."""
import json
import os
import unittest

from helpers import TMP, Base, m, state_file


class Validation(Base):
    def test_values_are_checked_against_their_definition(self):
        self.assertTrue(m._valid_setting("language", "pl"))
        self.assertFalse(m._valid_setting("language", "xx"))
        self.assertTrue(m._valid_setting("clock_after_min", 30))
        self.assertFalse(m._valid_setting("clock_after_min", 0))      # below the minimum
        self.assertFalse(m._valid_setting("clock_after_min", 61))     # above the maximum
        self.assertTrue(m._valid_setting("night_after_min", 0))       # 0 = never
        self.assertTrue(m._valid_setting("show_cover", False))
        self.assertFalse(m._valid_setting("not_a_setting", 1))

    def test_a_boolean_is_not_a_number_or_a_choice(self):
        # True == 1 in Python: it must not pass for volume_step (a choice) or clock_after_min (a number)
        self.assertFalse(m._valid_setting("volume_step", True))
        self.assertFalse(m._valid_setting("clock_after_min", True))
        self.assertFalse(m._valid_setting("show_cover", 1))           # a switch must be a real boolean
        self.assertFalse(m._valid_setting("show_analyser", 0))

    def test_definitions_are_consistent(self):
        keys = [sd["key"] for sd in m.SETTING_DEFS if "key" in sd]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(m.SETTING_KEYS))
        for tab in ("general", "display"):
            self.assertTrue(m.settings_rows(tab))
        for key in keys:
            self.assertIn(key, m.DEFAULTS, key)                       # every setting has a default
        self.assertEqual(m.LANGUAGE_CODES, list(m.STRINGS))


class Storage(Base):
    def test_load_ignores_what_is_invalid(self):
        with open(state_file("settings.json"), "w") as f:
            json.dump({"clock_after_min": 9, "language": "de", "night_after_min": 999, "bogus": 1}, f)
        self.assertEqual(m.load_settings(TMP), {"clock_after_min": 9, "language": "de"})

    def test_a_corrupt_or_missing_file_is_empty(self):
        self.assertEqual(m.load_settings(TMP), {})
        with open(state_file("settings.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(m.load_settings(TMP), {})
        with open(state_file("settings.json"), "w") as f:
            f.write("[1, 2]")
        self.assertEqual(m.load_settings(TMP), {})

    def test_save_is_atomic_and_empty_removes_the_file(self):
        self.assertTrue(m.save_settings(TMP, {"language": "fr"}))
        with open(state_file("settings.json")) as f:
            self.assertEqual(json.load(f), {"language": "fr"})
        self.assertFalse(os.path.exists(state_file("settings.json.tmp")))
        self.assertTrue(m.save_settings(TMP, {}))
        self.assertFalse(os.path.exists(state_file("settings.json")))

    def test_an_unwritable_directory_returns_false(self):
        self.assertFalse(m.save_settings("/proc/no-such-dir", {"language": "fr"}))


class Precedence(Base):
    def test_settings_json_wins_over_config_json_and_defaults(self):
        base = m.load_config(use_settings=False)
        self.assertEqual(base["clock_after_min"], 7)                  # config.json beats the default of 5
        self.assertEqual(base["night_after_min"], m.DEFAULTS["night_after_min"])
        with open(state_file("settings.json"), "w") as f:
            json.dump({"clock_after_min": 9, "language": "de"}, f)
        cfg = m.load_config()
        self.assertEqual(cfg["clock_after_min"], 9)                   # settings.json beats config.json
        self.assertEqual(cfg["language"], "de")
        self.assertEqual(cfg["night_after_min"], m.DEFAULTS["night_after_min"])

    def test_the_token_is_untouched_by_settings(self):
        self.assertTrue(m.save_settings(TMP, {"language": "fr"}))
        self.assertEqual(m.load_config()["ma_token"], "secret")

    def test_calibration_keeps_the_mode_of_an_existing_config(self):
        if not hasattr(os, "chmod") or os.name == "nt":
            self.skipTest("file modes are not meaningful here")
        path = os.environ["MA_DISPLAY_CONFIG"]
        os.chmod(path, 0o640)
        m.save_calibration({"swap_xy": True, "x_min": 1, "x_max": 2, "y_min": 3, "y_max": 4})
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o640)        # not world-readable: it may hold the token
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["ma_token"], "secret")
        self.assertEqual(data["calibration"]["x_max"], 2)


if __name__ == "__main__":
    unittest.main()
