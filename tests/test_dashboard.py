import importlib.util
import unittest
from pathlib import Path


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "optional Streamlit dependency not installed")
class DashboardTests(unittest.TestCase):
    def app(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=20)
        self.assertFalse(app.exception)
        return app

    def test_default_dashboard_waits_at_zero_and_displays_selected_time(self):
        app = self.app()
        self.assertEqual(app.session_state.result.scenario, "normal")
        self.assertEqual(len(app.metric), 4)
        self.assertEqual(app.metric[1].value, "-5.0 °C")
        self.assertEqual(app.metric[2].value, "0.00 W")
        self.assertFalse(app.session_state.playback.playing)
        next(b for b in app.button if b.label == "시뮬레이션 실행").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(app.session_state.playback.playing)
        app.session_state.playback.pause(app.session_state.playback.last_wall_s)
        app.session_state.playback.position_s = 180.0
        app.run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[1].value, "+5.0 °C")
        self.assertGreater(float(app.metric[3].value.split()[0]), 0.0)

    def test_fault_scenario_form_updates_results(self):
        app = self.app()
        app.selectbox[0].select("sensor_open")
        next(b for b in app.button if b.label == "시뮬레이션 실행").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.result.scenario, "sensor_open")
        self.assertIn("SENSOR_FAULT", app.session_state.result.summary()["fault_codes_seen"])

    def test_zero_budget_form_produces_no_heat(self):
        app = self.app()
        next(s for s in app.slider if s.label == "히터 평균 전력 예산 (W)").set_value(0.0)
        next(b for b in app.button if b.label == "시뮬레이션 실행").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state.result.summary()["heater_energy_wh"], 0.0)

    def test_invalid_limits_show_error_without_rerunning(self):
        app = self.app()
        next(n for n in app.number_input if n.label == "공급원 순간 전류 한도 (A)").set_value(1.0)
        next(b for b in app.button if b.label == "시뮬레이션 실행").click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
