"""Rev.C acceptance tests: expected safety behavior is independent of plant tuning."""
import json
import math
import random
import unittest
from dataclasses import replace

from thermallink.adapters import ESP32Adapter, decode_input, encode_input, encode_output
from thermallink.models import (Command, Config, FaultCode, PANELS, PanelId, Presence,
                                SensorError, SensorReading, StepInput, SystemState, PowerStatus)
from thermallink.power import PowerInput, allocate_power
from thermallink.safety import SafetyController
from thermallink.simulation import SCENARIOS, run_simulation
from thermallink.virtual import VirtualAdapter


class Rig:
    def __init__(self, config=None):
        self.cfg = config or Config()
        self.controller = SafetyController(self.cfg)
        self.time = 0.0
        self.modules = dict.fromkeys(PANELS, Presence.ATTACHED)
        self.temperatures = dict.fromkeys(PANELS, 30.0)

    def step(self, command=Command.NONE, duty=80.0, overrides=None, source=12.0, bus=12.0):
        sensors = {p: SensorReading(self.temperatures[p], self.time) for p in PANELS if self.modules[p] == Presence.ATTACHED}
        sensors.update(overrides or {})
        inp = StepInput(self.time, source, bus, sensors, dict(self.modules), duty, command)
        result = self.controller.step(inp)
        self.time += 0.5
        return result

    def start(self):
        self.step(Command.BOOT)
        for _ in range(4):
            self.step()
        return self.step(Command.START)


class PowerTests(unittest.TestCase):
    def power(self, cfg=None, duty=1.0, attached=frozenset(PANELS), source=12.0, bus=12.0):
        return allocate_power(cfg or Config(), PowerInput(source, bus, duty, attached))[0]

    def test_f01_f03_average_budget(self):
        o = self.power()
        self.assertEqual(o.status, PowerStatus.POWER_LIMITED)
        self.assertAlmostEqual(o.power_budget_w, 24)
        self.assertAlmostEqual(o.duty_applied, 0.8)
        self.assertAlmostEqual(o.power_actual_w, 24)

    def test_f02_five_resistive_panels(self):
        o = self.power()
        self.assertEqual(list(o.panel_on_power_w.values()), [12, 6, 6, 3, 3])
        self.assertAlmostEqual(o.bus_on_current_a, 2.5)
        self.assertAlmostEqual(sum(o.panel_share.values()), 1.0)

    def test_source_continuous_current_caps_average(self):
        o = self.power(replace(Config(), source_cont_current_a=1.0))
        self.assertEqual(o.status, PowerStatus.POWER_LIMITED)
        self.assertAlmostEqual(o.power_budget_w, 9.9)
        self.assertAlmostEqual(o.source_avg_current_a, 1.0)

    def test_source_power_idle_and_efficiency_once(self):
        cfg = replace(Config(), source_cont_power_w=20.0, idle_input_power_w=2.0, power_efficiency=0.8,
                      source_peak_current_a=4.0)
        o = self.power(cfg)
        self.assertAlmostEqual(o.power_budget_w, 14.4)
        self.assertAlmostEqual(o.source_avg_current_a * 12, 20)

    def test_source_and_bus_are_distinct(self):
        o = self.power(source=10.0, bus=11.0)
        self.assertAlmostEqual(sum(o.panel_on_power_w.values()), 30 * (11 / 12) ** 2)
        self.assertAlmostEqual(o.source_on_current_a, (sum(o.panel_on_power_w.values()) / 0.9 + 1) / 10)

    def test_h14_peak_is_blocked_even_at_tiny_duty(self):
        for duty in (0.0, 0.001, 0.5, 1.0):
            with self.subTest(duty=duty):
                o = self.power(duty=duty, bus=13.0)
                self.assertEqual(o.status, PowerStatus.PEAK_LIMIT)
                self.assertEqual(o.duty_applied, 0)

    def test_bus_peak_separate_from_source_peak(self):
        o = self.power(replace(Config(), bus_peak_current_a=2.0))
        self.assertEqual(o.fault.code, FaultCode.PEAK_LIMIT)

    def test_zero_load_and_share(self):
        o = self.power(attached=frozenset())
        self.assertEqual(o.duty_applied, 0)
        self.assertEqual(sum(o.panel_share.values()), 0)

    def test_zero_budget_blocks_output(self):
        o = self.power(replace(Config(), heater_max_avg_power_w=0.0))
        self.assertEqual(o.status, PowerStatus.NO_POWER)
        self.assertEqual(o.power_actual_w, 0)

    def test_idle_exceeds_source_budget(self):
        o = self.power(replace(Config(), source_cont_power_w=0.5))
        self.assertEqual(o.power_budget_w, 0)
        self.assertEqual(o.duty_applied, 0)

    def test_bad_duty_rejected(self):
        for duty in (float("nan"), float("inf"), -0.1, 1.01, True):
            with self.subTest(duty=duty):
                o = self.power(duty=duty)
                self.assertEqual(o.duty_applied, 0)
                self.assertEqual(o.fault.code, FaultCode.INPUT_INVALID)

    def test_config_rejected_not_defaulted(self):
        for cfg in (replace(Config(), power_efficiency=0), replace(Config(), heater_max_avg_power_w=-1),
                    replace(Config(), source_cont_power_w=float("nan")), replace(Config(), kp="bad")):
            with self.subTest(cfg=cfg):
                o = self.power(cfg)
                self.assertEqual(o.status, PowerStatus.CONFIG_ERROR)
                self.assertEqual(o.duty_applied, 0)

    def test_old_four_panel_config_rejected(self):
        specs = dict(Config().panel_specs)
        specs.pop(PanelId.CHEST_R)
        self.assertEqual(self.power(replace(Config(), panel_specs=specs)).status, PowerStatus.CONFIG_ERROR)

    def test_negative_or_zero_resistance_rejected(self):
        for resistance in (0, -1, float("nan"), float("inf")):
            specs = dict(Config().panel_specs)
            specs[PanelId.BACK] = replace(specs[PanelId.BACK], resistance_ohm=resistance)
            self.assertEqual(self.power(replace(Config(), panel_specs=specs)).status, PowerStatus.CONFIG_ERROR)

    def test_h13_invalid_voltages(self):
        for v in (0, -1, 8.9, 13.1, float("inf"), float("nan")):
            for side in ("source", "bus"):
                with self.subTest(v=v, side=side):
                    o = self.power(**{side: v})
                    self.assertEqual(o.fault.code, FaultCode.VOLTAGE_FAULT)

    def test_numeric_overflow_cannot_leak_nan_to_output(self):
        specs = dict(Config().panel_specs)
        specs[PanelId.BACK] = replace(specs[PanelId.BACK], resistance_ohm=1e-310)
        cfg = replace(Config(), panel_specs=specs)
        o = self.power(cfg)
        self.assertEqual(o.status, PowerStatus.CONFIG_ERROR)
        self.assertEqual(o.power_actual_w, 0)
        r = Rig(cfg)
        output = r.step(Command.BOOT)
        self.assertEqual(output.estimated_power_w, 0)
        plant = VirtualAdapter(cfg)
        plant.write_output(output)
        self.assertEqual(plant.advance(0.5).power_actual_w, 0)

    def test_random_power_invariants(self):
        rng = random.Random(20261107)
        for _ in range(300):
            cfg = replace(Config(), heater_max_avg_power_w=rng.uniform(0, 35), source_cont_current_a=rng.uniform(0.2, 3.0))
            duty = rng.random()
            o = self.power(cfg, duty=duty, attached=frozenset(p for p in PANELS if rng.random() > 0.25),
                           source=rng.uniform(9, 13), bus=rng.uniform(9, 13))
            self.assertLessEqual(o.duty_applied, duty + 1e-10)
            self.assertLessEqual(o.power_actual_w, o.power_budget_w + 1e-9)
            self.assertGreaterEqual(o.power_actual_w, 0)
            if not o.fault:
                self.assertLessEqual(o.source_avg_current_a, cfg.source_cont_current_a + 1e-9)


class SafetyTests(unittest.TestCase):
    def test_h01_h03_boot_ready_start(self):
        r = Rig()
        self.assertEqual(r.step(Command.BOOT).system_state, SystemState.SELF_TEST)
        self.assertEqual(r.step(Command.START).applied_duty_pct, 0)
        for _ in range(3):
            o = r.step()
        self.assertEqual(o.system_state, SystemState.READY)
        self.assertEqual(o.applied_duty_pct, 0)
        self.assertEqual(r.step(Command.START).system_state, SystemState.RUNNING)

    def test_c05_percent_conversion(self):
        r = Rig()
        r.start()
        o = r.step(duty=25)
        self.assertEqual(o.power.duty_requested, 0.25)
        self.assertEqual(o.applied_duty_pct, 25)

    def test_stop_and_off_do_not_heat(self):
        r = Rig()
        r.start()
        self.assertEqual(r.step(Command.STOP).system_state, SystemState.READY)
        r.step(Command.START)
        self.assertEqual(r.step(Command.OFF).applied_duty_pct, 0)

    def test_h06_detached_glove_at_boot_excluded(self):
        r = Rig()
        r.modules[PanelId.GLOVE_L] = Presence.DETACHED
        o = r.start()
        self.assertEqual(o.system_state, SystemState.RUNNING)
        self.assertEqual(o.estimated_panel_power_w[PanelId.GLOVE_L], 0)
        self.assertEqual(o.latched_faults, ())

    def test_h07_runtime_disconnect_holds_all(self):
        r = Rig()
        r.start()
        r.modules[PanelId.GLOVE_L] = Presence.DETACHED
        o = r.step(Command.START)
        self.assertEqual(o.system_state, SystemState.RECONNECT_HOLD)
        self.assertEqual(o.applied_duty_pct, 0)
        self.assertEqual(o.estimated_power_w, 0)
        self.assertEqual(o.latched_faults, ())

    def test_h09_reconnect_needs_stability_and_new_start(self):
        r = Rig()
        r.start()
        r.modules[PanelId.GLOVE_L] = Presence.DETACHED
        r.step()
        self.assertEqual(r.step(Command.START).system_state, SystemState.RECONNECT_HOLD)
        for _ in range(5):
            self.assertEqual(r.step().applied_duty_pct, 0)
        self.assertEqual(r.step(Command.START).system_state, SystemState.RUNNING)
        r.modules[PanelId.GLOVE_L] = Presence.ATTACHED
        self.assertEqual(r.step().system_state, SystemState.RECONNECT_HOLD)

    def test_h08_unknown_or_unstable_contact_is_fault(self):
        for presence in (Presence.UNSTABLE, Presence.UNKNOWN):
            r = Rig()
            r.start()
            r.modules[PanelId.GLOVE_R] = presence
            o = r.step()
            self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)
            self.assertIn(FaultCode.MODULE_FAULT, [f.code for f in o.latched_faults])

    def test_fixed_panel_may_not_be_detached(self):
        r = Rig()
        r.modules[PanelId.CHEST_L] = Presence.DETACHED
        self.assertEqual(r.start().system_state, SystemState.FAULT_LATCHED)

    def test_h05_all_five_sensors_open_short_range_no_sample(self):
        for pid in PANELS:
            for error in (SensorError.OPEN, SensorError.SHORT, SensorError.RANGE, SensorError.NO_SAMPLE):
                with self.subTest(pid=pid, error=error):
                    r = Rig()
                    r.start()
                    o = r.step(overrides={pid: SensorReading(None, r.time, False, error)})
                    self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)
                    self.assertEqual(o.estimated_power_w, 0)

    def test_missing_attached_sensor(self):
        r = Rig()
        r.start()
        sensors = {p: SensorReading(30, r.time) for p in PANELS if p != PanelId.CHEST_R}
        o = r.controller.step(StepInput(r.time, 12, 12, sensors, r.modules, 50))
        self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)

    def test_stale_future_nonfinite_sensor_samples(self):
        for temp, timestamp in ((30, -1), (30, 100), (float("nan"), 3), (30, float("nan")), (101, 3)):
            r = Rig()
            r.start()
            o = r.step(overrides={PanelId.GLOVE_L: SensorReading(temp, timestamp)})
            self.assertEqual(o.applied_duty_pct, 0)
            self.assertTrue(o.latched_faults)

    def test_h04_any_ntc_overtemp_latches(self):
        for pid in PANELS:
            r = Rig()
            r.start()
            r.temperatures[pid] = 48
            o = r.step()
            self.assertEqual(o.applied_duty_pct, 0)
            self.assertIn(FaultCode.OVER_TEMP, [f.code for f in o.latched_faults])

    def test_h12_reset_requires_cause_cleared_and_release_temperature(self):
        r = Rig()
        r.start()
        r.temperatures[PanelId.BACK] = 49
        r.step()
        self.assertEqual(r.step(Command.RESET).system_state, SystemState.FAULT_LATCHED)
        r.temperatures[PanelId.BACK] = 45
        self.assertEqual(r.step(Command.RESET).system_state, SystemState.FAULT_LATCHED)
        r.temperatures[PanelId.BACK] = 42
        self.assertEqual(r.step().system_state, SystemState.FAULT_LATCHED)
        o = r.step(Command.RESET)
        self.assertEqual(o.system_state, SystemState.READY)
        self.assertEqual(o.applied_duty_pct, 0)
        self.assertEqual(r.step(Command.START).system_state, SystemState.RUNNING)

    def test_off_boot_cannot_clear_fault(self):
        r = Rig()
        r.start()
        r.step(overrides={PanelId.CHEST_L: SensorReading(None, r.time, False, SensorError.OPEN)})
        self.assertTrue(r.step(Command.OFF).latched_faults)
        self.assertEqual(r.step(Command.BOOT).system_state, SystemState.FAULT_LATCHED)

    def test_bad_control_timestamp_fails_closed(self):
        for time in (2.5, 1.0, 8.0, float("nan")):
            r = Rig()
            r.start()
            r.time = time
            o = r.step()
            self.assertEqual(o.applied_duty_pct, 0)
            self.assertIn(FaultCode.CONTROL_TIMING, [f.code for f in o.latched_faults])

    def test_average_power_limiting_keeps_running(self):
        r = Rig(replace(Config(), heater_max_avg_power_w=5))
        o = r.start()
        self.assertEqual(o.system_state, SystemState.RUNNING)
        self.assertEqual(o.power.status, PowerStatus.POWER_LIMITED)
        self.assertAlmostEqual(o.estimated_power_w, 5)

    def test_peak_failure_latches(self):
        r = Rig()
        r.start()
        o = r.step(bus=13, duty=1)
        self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)
        self.assertEqual(o.applied_duty_pct, 0)
        self.assertEqual(r.step().system_state, SystemState.FAULT_LATCHED)

    def test_bad_config_fails_closed_in_safety_gate(self):
        for cfg in (replace(Config(), temp_cutoff_c="bad"), replace(Config(), power_efficiency=float("nan"))):
            o = Rig(cfg).step(Command.BOOT)
            self.assertEqual(o.applied_duty_pct, 0)
            self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)


class PlantAndContractTests(unittest.TestCase):
    def test_h10_open_switch_only_removes_that_panel(self):
        cfg = Config()
        plant = VirtualAdapter(cfg)
        rig = Rig(cfg)
        plant.write_output(rig.start())
        plant.switch_temperatures[PanelId.GLOVE_L] = 60
        plant.switch_hotspot_c[PanelId.GLOVE_L] = 40
        result = plant.advance(0.5)
        self.assertEqual(result.panel_power_w[PanelId.GLOVE_L], 0)
        self.assertGreater(result.panel_power_w[PanelId.BACK], 0)
        self.assertFalse(plant.switch_closed[PanelId.GLOVE_L])
        self.assertFalse(rig.controller.latched_faults)

    def test_h11_switch_returns_independently_while_pwm_off(self):
        plant = VirtualAdapter(Config())
        plant.switch_closed[PanelId.GLOVE_L] = False
        plant.switch_temperatures[PanelId.GLOVE_L] = 40
        result = plant.advance(0.5)
        self.assertTrue(plant.switch_closed[PanelId.GLOVE_L])
        self.assertEqual(result.power_actual_w, 0)
        self.assertIn("SWITCH_CLOSED:GLOVE_L", result.switch_events)

    def test_hidden_switch_does_not_reduce_current_check(self):
        cfg = replace(Config(), bus_peak_current_a=2.3)
        plant = VirtualAdapter(cfg)
        plant.switch_closed[PanelId.GLOVE_L] = False
        plant.switch_closed[PanelId.GLOVE_R] = False
        o = SafetyController(cfg).step(plant.read_inputs(0, 1, Command.BOOT))
        self.assertEqual(o.power.bus_on_current_a, 2.5)
        self.assertEqual(o.system_state, SystemState.FAULT_LATCHED)

    def test_heat_equation_matches_analytic_solution(self):
        cfg = Config()
        plant = VirtualAdapter(cfg)
        result = plant.advance(1.0)
        spec = cfg.panel_specs[PanelId.BACK]
        g = spec.ambient_conductance_w_per_c + spec.body_conductance_w_per_c
        equilibrium = (spec.ambient_conductance_w_per_c * cfg.ambient_temp_c + spec.body_conductance_w_per_c * cfg.body_temp_c) / g
        expected = equilibrium + (cfg.initial_temp_c - equilibrium) * math.exp(-g / spec.heat_capacity_j_per_c)
        self.assertAlmostEqual(plant.temperatures[PanelId.BACK], expected, places=10)
        self.assertEqual(result.power_actual_w, 0)

    def test_h15_logical_json_roundtrip(self):
        plant = VirtualAdapter(Config())
        inp = plant.read_inputs(0, 37.5, Command.BOOT)
        self.assertEqual(decode_input(encode_input(inp)), inp)
        self.assertNotIn("switch", encode_input(inp))
        a = SafetyController(Config()).step(inp)
        b = SafetyController(Config()).step(decode_input(encode_input(inp)))
        self.assertEqual(encode_output(a), encode_output(b))

    def test_h15_esp32_physical_port_is_explicitly_unimplemented(self):
        with self.assertRaises(NotImplementedError):
            ESP32Adapter().read_inputs(0, 50)

    def test_json_rejects_nonfinite_or_wrong_contract(self):
        with self.assertRaises(ValueError):
            decode_input('{"contract":"old"}')
        with self.assertRaises(ValueError):
            decode_input('{"contract":"TL-REVC-5P-1PWM-v1", "timestamp_s":NaN}')

    def test_all_scenarios_final_gate_and_budget_invariants(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = run_simulation(scenario, 150)
                for row in result.rows:
                    self.assertLessEqual(row["actual_power_w"], row["budget_w"] + 1e-8)
                    self.assertLessEqual(row["actual_power_w"], row["estimated_power_w"] + 1e-8)
                    if row["system_state"] != "RUNNING":
                        self.assertEqual(row["applied_duty_pct"], 0)
                        self.assertEqual(row["actual_power_w"], 0)

    def test_normal_pi_reaches_target_without_fault(self):
        result = run_simulation("normal", 1200)
        self.assertFalse(result.summary()["fault_codes_seen"])
        self.assertAlmostEqual(result.rows[-1]["temp_BACK_c"], 38, delta=0.6)

    def test_scenario_switch_cycle_has_no_software_latch(self):
        result = run_simulation("switch_cycle", 150)
        events = [e["message"] for e in result.events]
        self.assertIn("SWITCH_OPEN:GLOVE_L", events)
        self.assertIn("SWITCH_CLOSED:GLOVE_L", events)
        self.assertFalse(result.summary()["fault_codes_seen"])


if __name__ == "__main__":
    unittest.main()
