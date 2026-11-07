from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path


class PanelId(StrEnum):
    BACK = "BACK"
    CHEST_L = "CHEST_L"
    CHEST_R = "CHEST_R"
    GLOVE_L = "GLOVE_L"
    GLOVE_R = "GLOVE_R"


PANELS = tuple(PanelId)
GLOVES = frozenset((PanelId.GLOVE_L, PanelId.GLOVE_R))


class Presence(StrEnum):
    ATTACHED = "ATTACHED"
    DETACHED = "DETACHED"
    UNSTABLE = "UNSTABLE"
    UNKNOWN = "UNKNOWN"


class SensorError(StrEnum):
    NONE = "NONE"
    OPEN = "OPEN"
    SHORT = "SHORT"
    RANGE = "RANGE"
    NO_SAMPLE = "NO_SAMPLE"


class SystemState(StrEnum):
    OFF = "OFF"
    SELF_TEST = "SELF_TEST"
    READY = "READY"
    RUNNING = "RUNNING"
    RECONNECT_HOLD = "RECONNECT_HOLD"
    FAULT_LATCHED = "FAULT_LATCHED"


class PowerStatus(StrEnum):
    NORMAL = "NORMAL"
    POWER_LIMITED = "POWER_LIMITED"
    IDLE = "IDLE"
    NO_POWER = "NO_POWER"
    CONFIG_ERROR = "CONFIG_ERROR"
    PEAK_LIMIT = "PEAK_LIMIT"


class Command(StrEnum):
    NONE = "NONE"
    BOOT = "BOOT"
    START = "START"
    STOP = "STOP"
    RESET = "RESET"
    OFF = "OFF"


class FaultCode(StrEnum):
    CONFIG_ERROR = "CONFIG_ERROR"
    INPUT_INVALID = "INPUT_INVALID"
    SENSOR_FAULT = "SENSOR_FAULT"
    OVER_TEMP = "OVER_TEMP"
    MODULE_FAULT = "MODULE_FAULT"
    VOLTAGE_FAULT = "VOLTAGE_FAULT"
    PEAK_LIMIT = "PEAK_LIMIT"
    CONTROL_TIMING = "CONTROL_TIMING"


def finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class PanelSpec:
    resistance_ohm: float
    heat_capacity_j_per_c: float
    ambient_conductance_w_per_c: float
    body_conductance_w_per_c: float
    rated_power_w: float | None = None


def default_panels() -> dict[PanelId, PanelSpec]:
    # Hypothetical resistances: 12/6/6/3/3 W at 12 V; NOT measured ratings.
    return {
        PanelId.BACK: PanelSpec(12.0, 90.0, 0.14, 0.16),
        PanelId.CHEST_L: PanelSpec(24.0, 70.0, 0.10, 0.10),
        PanelId.CHEST_R: PanelSpec(24.0, 70.0, 0.10, 0.10),
        PanelId.GLOVE_L: PanelSpec(48.0, 25.0, 0.06, 0.03),
        PanelId.GLOVE_R: PanelSpec(48.0, 25.0, 0.06, 0.03),
    }


@dataclass(frozen=True)
class Config:
    panel_specs: dict[PanelId, PanelSpec] = field(default_factory=default_panels)
    source_cont_current_a: float = 3.0
    source_cont_power_w: float = 36.0
    source_peak_current_a: float = 3.2
    bus_peak_current_a: float = 3.5
    heater_max_avg_power_w: float = 24.0
    idle_input_power_w: float = 1.0
    power_efficiency: float = 0.90
    source_min_voltage_v: float = 9.0
    source_max_voltage_v: float = 13.0
    bus_min_voltage_v: float = 9.0
    bus_max_voltage_v: float = 13.0
    temp_cutoff_c: float = 48.0
    temp_release_c: float = 42.0
    sensor_min_c: float = -40.0
    sensor_max_c: float = 100.0
    sensor_max_age_s: float = 1.5
    control_period_s: float = 0.5
    max_control_gap_s: float = 2.0
    connection_stable_s: float = 2.0
    switch_trip_c: float = 55.0
    switch_release_c: float = 48.0
    target_temp_c: float = 38.0
    kp: float = 0.12
    ki: float = 0.004
    ambient_temp_c: float = 5.0
    ambient_profile: str = "constant"
    ambient_min_c: float = -5.0
    ambient_max_c: float = 5.0
    ambient_ramp_s: float = 180.0
    body_temp_c: float = 32.0
    initial_temp_c: float = 28.0
    assumption_note: str = "SIMULATION ONLY: all electrical and thermal values are unverified assumptions."

    def errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        numeric = {k: v for k, v in asdict(self).items() if k not in ("panel_specs", "assumption_note", "ambient_profile")}
        for name, value in numeric.items():
            if not finite(value):
                errors.append(f"{name}: finite number required")
        if errors:
            return tuple(errors)
        if self.ambient_profile not in ("constant", "triangle"):
            errors.append("ambient_profile must be constant or triangle")
        if self.ambient_min_c >= self.ambient_max_c or self.ambient_ramp_s <= 0:
            errors.append("ambient_min_c < ambient_max_c and ambient_ramp_s > 0 required")
        positive = ("source_cont_current_a", "source_cont_power_w", "source_peak_current_a",
                    "bus_peak_current_a", "source_min_voltage_v", "source_max_voltage_v",
                    "bus_min_voltage_v", "bus_max_voltage_v", "sensor_max_age_s",
                    "control_period_s", "max_control_gap_s", "connection_stable_s")
        for name in positive:
            if getattr(self, name) <= 0:
                errors.append(f"{name}: must be > 0")
        for name in ("heater_max_avg_power_w", "idle_input_power_w", "kp", "ki"):
            if getattr(self, name) < 0:
                errors.append(f"{name}: must be >= 0")
        if not 0 < self.power_efficiency <= 1:
            errors.append("power_efficiency: must be in (0, 1]")
        if not self.sensor_min_c < self.temp_release_c < self.temp_cutoff_c < self.sensor_max_c:
            errors.append("require sensor_min < temp_release < temp_cutoff < sensor_max")
        if not self.sensor_min_c <= self.target_temp_c < self.temp_cutoff_c:
            errors.append("target_temp_c must be inside sensor range and below cutoff")
        if not self.switch_release_c < self.switch_trip_c:
            errors.append("switch_release_c must be below switch_trip_c")
        for prefix in ("source", "bus"):
            if getattr(self, f"{prefix}_min_voltage_v") >= getattr(self, f"{prefix}_max_voltage_v"):
                errors.append(f"{prefix} voltage range invalid")
        if self.source_peak_current_a < self.source_cont_current_a:
            errors.append("source_peak_current_a must be >= continuous current limit")
        if self.max_control_gap_s < self.control_period_s:
            errors.append("max_control_gap_s must be >= control_period_s")
        if set(self.panel_specs) != set(PANELS):
            errors.append("panel_specs must contain exactly the five Rev.C panel IDs")
        for pid, spec in self.panel_specs.items():
            if not isinstance(spec, PanelSpec):
                errors.append(f"{pid}: PanelSpec required")
                continue
            for name in ("resistance_ohm", "heat_capacity_j_per_c", "ambient_conductance_w_per_c", "body_conductance_w_per_c"):
                value = getattr(spec, name)
                if not finite(value) or value <= 0:
                    errors.append(f"{pid}.{name}: positive finite number required")
            if spec.rated_power_w is not None and (not finite(spec.rated_power_w) or spec.rated_power_w <= 0):
                errors.append(f"{pid}.rated_power_w: positive number or null required")
        return tuple(errors)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if "panel_specs" in raw:
            raw["panel_specs"] = {PanelId(k): PanelSpec(**v) for k, v in raw["panel_specs"].items()}
        config = cls(**raw)
        if config.errors():
            raise ValueError("; ".join(config.errors()))
        return config

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class SensorReading:
    temperature_c: float | None
    timestamp_s: float
    sensor_valid: bool = True
    error: SensorError = SensorError.NONE


@dataclass(frozen=True)
class StepInput:
    timestamp_s: float
    source_voltage_v: float
    bus_voltage_v: float
    sensors: dict[PanelId, SensorReading]
    modules: dict[PanelId, Presence]
    requested_duty_pct: float
    command: Command = Command.NONE


@dataclass(frozen=True)
class Fault:
    code: FaultCode
    detail: str


def zero_panels() -> dict[PanelId, float]:
    return dict.fromkeys(PANELS, 0.0)
