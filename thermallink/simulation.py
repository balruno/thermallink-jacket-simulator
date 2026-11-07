from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .controller import TemperatureController
from .environment import ambient_temperature
from .models import (Command, Config, PANELS, PanelId, Presence, SensorError,
                     SensorReading, SystemState, finite)
from .safety import SafetyController
from .virtual import VirtualAdapter


SCENARIOS = {
    "normal": ("정상 자동 제어", "0초 BOOT → 3초 START. 등판 38°C 목표, 공통 PWM 1채널."),
    "power_limited": ("평균 전력 부족", "평균 히터 예산을 최대 8W로 제한. POWER_LIMITED 상태에서 운전 지속."),
    "glove_detach": ("운전 중 왼쪽 장갑 분리", "60초 분리 → 전체 정지/HOLD → 100초 별도 START. 왼쪽 장갑은 계속 분리."),
    "both_detached": ("양쪽 장갑 없이 시작", "부팅 전 양쪽 장갑 분리. 본체 패드 3개만 가열."),
    "sensor_open": ("오른쪽 가슴 NTC 단선", "60~90초 센서 OPEN. 100초 RESET → 110초 START."),
    "stale_sample": ("장갑 NTC 샘플 지연", "60~90초 왼쪽 장갑 샘플 시각을 59초로 고정. 100초 RESET → 110초 START."),
    "overtemp": ("소프트웨어 과열 차단", "60~90초 등판 NTC에 51°C 주입. 100초 RESET → 110초 START."),
    "peak_current": ("PWM ON 순간 과전류", "60~90초 히터 버스만 13V. 공급원 ON 전류가 기본 3.2A 한도를 넘으면 차단."),
    "voltage_fault": ("공급 전압 이상", "60~90초 공급원/버스를 8V로 변경. 100초 RESET → 110초 START."),
    "switch_cycle": ("55°C 물리 스위치 개방·복귀", "60~120초 왼쪽 장갑 스위치 위치에 +40°C 국부 열 주입. NTC와 별도 모델."),
    "unstable_contact": ("장갑 접점 불안정", "60~65초 오른쪽 장갑 UNSTABLE → 정상 복귀. 오류는 100초 RESET까지 유지."),
}


@dataclass
class SimulationResult:
    scenario: str
    config: Config
    rows: list[dict]
    events: list[dict]
    controller_kind: str = "pi"
    model_metadata: dict | None = None

    def summary(self) -> dict:
        return {
            "scenario": self.scenario, "title": SCENARIOS[self.scenario][0],
            "controller_kind": self.controller_kind,
            "ambient_profile": self.config.ambient_profile,
            "duration_s": self.rows[-1]["interval_end_s"],
            "final_state": self.rows[-1]["system_state"],
            "max_actual_power_w": max(r["actual_power_w"] for r in self.rows),
            "max_applied_duty_pct": max(r["applied_duty_pct"] for r in self.rows),
            "max_sensor_temperature_c": max(r[f"temp_{p}_c"] for r in self.rows for p in PANELS),
            "heater_energy_wh": self.rows[-1]["heater_energy_wh"],
            "fault_codes_seen": sorted({code for r in self.rows for code in r["fault_codes"].split("|") if code}),
            "states_seen": list(dict.fromkeys(r["system_state"] for r in self.rows)),
        }


def run_simulation(scenario: str = "normal", duration_s: float = 600.0,
                   config: Config | None = None, manual_duty_pct: float | None = None,
                   controller_kind: str = "pi", model_path: str | None = None) -> SimulationResult:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    if controller_kind not in ("pi", "neural"):
        raise ValueError("controller_kind must be pi or neural")
    if not finite(duration_s) or not 0 < duration_s <= 86400:
        raise ValueError("duration must be in (0, 86400] seconds")
    if manual_duty_pct is not None and (not finite(manual_duty_pct) or not 0 <= manual_duty_pct <= 100):
        raise ValueError("manual PWM must be in [0, 100]")
    cfg = config or Config()
    if scenario == "power_limited":
        cfg = replace(cfg, heater_max_avg_power_w=min(cfg.heater_max_avg_power_w, 8.0))
    if cfg.errors():
        raise ValueError("; ".join(cfg.errors()))
    if duration_s / cfg.control_period_s > 200000:
        raise ValueError("simulation limited to 200000 control steps")
    plant = VirtualAdapter(cfg)
    supervisor = SafetyController(cfg)
    controller = TemperatureController(cfg)
    model_metadata = None
    if controller_kind == "neural" and manual_duty_pct is None:
        from .neural import NeuralController
        controller = NeuralController(cfg, model_path)
        model_metadata = controller.model["metadata"]
    if scenario == "both_detached":
        plant.modules[PanelId.GLOVE_L] = plant.modules[PanelId.GLOVE_R] = Presence.DETACHED
    commands = [(0.0, Command.BOOT), (max(3.0, cfg.connection_stable_s + cfg.control_period_s), Command.START)]
    if scenario == "glove_detach":
        commands += [(100.0, Command.START)]
    elif scenario in ("sensor_open", "stale_sample", "overtemp", "peak_current", "voltage_fault", "unstable_contact"):
        commands += [(100.0, Command.RESET), (110.0, Command.START)]
    commands.sort()
    command_index = 0
    rows, events = [], []
    energy_wh = 0.0
    previous_faults: tuple = ()
    n = math.ceil(duration_s / cfg.control_period_s)
    for i in range(n):
        now = i * cfg.control_period_s
        dt = min(cfg.control_period_s, duration_s - now)
        command = Command.NONE
        if command_index < len(commands) and now + 1e-9 >= commands[command_index][0]:
            command = commands[command_index][1]
            command_index += 1
        plant.sensor_overrides.clear()
        plant.source_voltage_v = plant.bus_voltage_v = 12.0
        if scenario == "glove_detach" and now >= 60:
            plant.modules[PanelId.GLOVE_L] = Presence.DETACHED
        if scenario == "unstable_contact":
            plant.modules[PanelId.GLOVE_R] = Presence.UNSTABLE if 60 <= now < 65 else Presence.ATTACHED
        if scenario == "sensor_open" and 60 <= now < 90:
            plant.sensor_overrides[PanelId.CHEST_R] = SensorReading(None, now, False, SensorError.OPEN)
        if scenario == "stale_sample" and 60 <= now < 90:
            plant.sensor_overrides[PanelId.GLOVE_L] = SensorReading(plant.temperatures[PanelId.GLOVE_L], 59.0)
        if scenario == "overtemp" and 60 <= now < 90:
            plant.sensor_overrides[PanelId.BACK] = SensorReading(51.0, now)
        if scenario == "peak_current" and 60 <= now < 90:
            plant.bus_voltage_v = 13.0
        if scenario == "voltage_fault" and 60 <= now < 90:
            plant.source_voltage_v = plant.bus_voltage_v = 8.0
        plant.switch_hotspot_c[PanelId.GLOVE_L] = 40.0 if scenario == "switch_cycle" and 60 <= now < 120 else 0.0
        observed = plant.read_inputs(now, 0.0, command)
        back = observed.sensors.get(PanelId.BACK)
        request = (manual_duty_pct if manual_duty_pct is not None else
                   controller.request_pct(back.temperature_c if back else None,
                                          supervisor.state == SystemState.RUNNING or command == Command.START))
        inp = replace(observed, requested_duty_pct=request)
        output = supervisor.step(inp)
        plant.write_output(output)
        controller.feedback(output.applied_duty_pct, dt, output.system_state == SystemState.RUNNING)
        before = dict(plant.temperatures)
        physics = plant.advance(dt, lambda t: ambient_temperature(cfg, t), now)
        energy_wh += physics.power_actual_w * dt / 3600.0
        fault_codes = "|".join(sorted({f.code.value for f in output.latched_faults}))
        row = {
            "time_s": now, "interval_end_s": now + dt, "system_state": output.system_state.value,
            "power_status": output.power.status.value, "command": command.value, "fault_codes": fault_codes,
            "controller_duty_pct": output.controller_duty_pct,
            "power_limited_duty_pct": output.power_limited_duty_pct,
            "applied_duty_pct": output.applied_duty_pct,
            "budget_w": output.power.power_budget_w,
            "estimated_power_w": output.estimated_power_w, "actual_power_w": physics.power_actual_w,
            "bus_on_current_a": output.power.bus_on_current_a,
            "source_on_current_a": output.power.source_on_current_a,
            "source_avg_current_a": physics.source_avg_current_a,
            "source_voltage_v": inp.source_voltage_v, "bus_voltage_v": inp.bus_voltage_v,
            "heater_energy_wh": energy_wh,
            "ambient_temp_c": ambient_temperature(cfg, now),
        }
        for p in PANELS:
            reading = inp.sensors.get(p)
            row[f"temp_{p}_c"] = reading.temperature_c if reading and finite(reading.temperature_c) else before[p]
            row[f"plant_{p}_c"] = before[p]
            row[f"sensor_{p}_valid"] = bool(reading and reading.sensor_valid and reading.error == SensorError.NONE
                                                    and finite(reading.temperature_c) and 0 <= now - reading.timestamp_s <= cfg.sensor_max_age_s)
            row[f"actual_{p}_w"] = physics.panel_power_w[p]
            row[f"switch_{p}_closed"] = plant.switch_closed[p]
            row[f"presence_{p}"] = plant.modules[p].value
        rows.append(row)
        for message in output.events + physics.switch_events:
            events.append({"time_s": now, "message": message})
        if output.latched_faults != previous_faults:
            events.append({"time_s": now, "message": "FAULTS: " + ("; ".join(f"{f.code}: {f.detail}" for f in output.latched_faults) or "CLEARED")})
            previous_faults = output.latched_faults
    return SimulationResult(scenario, cfg, rows, events,
                            "manual" if manual_duty_pct is not None else controller_kind, model_metadata)
