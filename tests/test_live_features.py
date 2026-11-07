import unittest
from dataclasses import replace

from thermallink.environment import ambient_temperature
from thermallink.heating_view import jacket_svg
from thermallink.models import Config
from thermallink.neural import load_model, predict_duty
from thermallink.playback import Playback
from thermallink.simulation import run_simulation


class LiveFeatureTests(unittest.TestCase):
    def test_linear_ambient_reverses_every_180_seconds_and_changes_heat_loss(self):
        cfg = replace(Config(), ambient_profile="triangle")
        self.assertEqual([ambient_temperature(cfg, t) for t in (0, 90, 180, 270, 360, 540)], [-5, 0, 5, 0, -5, 5])
        moving = run_simulation("normal", 10, cfg, manual_duty_pct=0)
        fixed = run_simulation("normal", 10, Config(), manual_duty_pct=0)
        self.assertLess(moving.rows[-1]["temp_BACK_c"], fixed.rows[-1]["temp_BACK_c"])

    def test_playback_time_pause_resume_speed_end(self):
        p = Playback(600, speed=10)
        p.start(100)
        self.assertEqual(p.tick(118), 180)
        p.pause(118)
        self.assertEqual(p.tick(200), 180)
        p.resume(200)
        p.set_speed(1, 210)
        self.assertEqual(p.tick(220), 290)
        p.tick(1000)
        self.assertEqual(p.position_s, 600)
        self.assertFalse(p.playing)

    def test_neural_and_effects_respect_final_gate(self):
        model = load_model()
        self.assertLess(model["metadata"]["validation_mae_pct_points"], 1.0)
        for error in (-20, -2, 0, 2, 20):
            self.assertTrue(0 <= predict_duty(model, error, 0.5) <= 1)
        result = run_simulation("peak_current", 62, replace(Config(), ambient_profile="triangle"), controller_kind="neural")
        active = next(row for row in result.rows if row["actual_power_w"] > 0)
        fault = result.rows[-1]
        self.assertIn("PEAK_LIMIT", fault["fault_codes"])
        self.assertEqual(fault["actual_power_w"], 0)
        self.assertEqual(jacket_svg(fault, 61).count('data-heating="true"'), 0)
        self.assertEqual(jacket_svg(active, 10).count('data-heating="true"'), 5)


if __name__ == "__main__":
    unittest.main()
