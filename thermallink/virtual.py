"""Five independent lumped thermal plants behind ONE common PWM channel."""
import math
from dataclasses import dataclass
from typing import Callable

from .models import (Command, Config, PANELS, PanelId, Presence, SensorError,
                     SensorReading, StepInput, SystemState, finite)
from .safety import StepOutput


@dataclass(frozen=True)
class PlantStep:
    panel_power_w: dict[PanelId, float]  # Actual average over this integration interval.
    power_actual_w: float
    source_avg_current_a: float
    switch_events: tuple[str, ...]


class VirtualAdapter:
    def __init__(self, config: Config):
        if config.errors():
            raise ValueError("; ".join(config.errors()))
        self.config = config
        self.temperatures = dict.fromkeys(PANELS, config.initial_temp_c)
        self.switch_temperatures = dict(self.temperatures)
        self.switch_closed = dict.fromkeys(PANELS, True)
        self.switch_hotspot_c = dict.fromkeys(PANELS, 0.0)
        self.modules = dict.fromkeys(PANELS, Presence.ATTACHED)
        self.source_voltage_v = 12.0
        self.bus_voltage_v = 12.0
        self.sensor_overrides: dict[PanelId, SensorReading] = {}
        self.applied_duty_pct = 0.0

    def read_inputs(self, timestamp_s: float, requested_duty_pct: float,
                    command: Command = Command.NONE) -> StepInput:
        sensors = {
            p: self.sensor_overrides.get(p, SensorReading(self.temperatures[p], timestamp_s))
            for p in PANELS if self.modules[p] == Presence.ATTACHED
        }
        # No switch_closed / switch temperature fields are visible to firmware.
        return StepInput(timestamp_s, self.source_voltage_v, self.bus_voltage_v,
                         sensors, dict(self.modules), requested_duty_pct, command)

    def write_output(self, output: StepOutput) -> None:
        duty = output.applied_duty_pct
        if not finite(duty) or not 0 <= duty <= 100:
            self.applied_duty_pct = 0.0
            raise ValueError("invalid physical PWM")
        self.applied_duty_pct = duty if output.system_state == SystemState.RUNNING and not output.latched_faults else 0.0

    def advance(self, dt: float, ambient_at: Callable[[float], float] | None = None,
                start_s: float = 0.0) -> PlantStep:
        if not finite(dt) or dt <= 0:
            raise ValueError("dt must be positive and finite")
        cfg = self.config
        steps = max(1, math.ceil(dt / 0.05))
        h = dt / steps
        energy_j = dict.fromkeys(PANELS, 0.0)
        events: list[str] = []
        for substep in range(steps):
            ambient = ambient_at(start_s + (substep + 0.5) * h) if ambient_at else cfg.ambient_temp_c
            if not finite(ambient):
                raise ValueError("ambient temperature must be finite")
            for pid in PANELS:
                spec = cfg.panel_specs[pid]
                # Separate thermostat bulb with a 5 s lag. Test-only local hotspot
                # models heat at the switch that the panel NTC does not measure.
                switch_target = self.temperatures[pid] + self.switch_hotspot_c[pid]
                self.switch_temperatures[pid] = switch_target + (self.switch_temperatures[pid] - switch_target) * math.exp(-h / 5.0)
                old = self.switch_closed[pid]
                if old and self.switch_temperatures[pid] >= cfg.switch_trip_c:
                    self.switch_closed[pid] = False
                elif not old and self.switch_temperatures[pid] <= cfg.switch_release_c:
                    self.switch_closed[pid] = True
                if old != self.switch_closed[pid]:
                    events.append(f"SWITCH_{'CLOSED' if self.switch_closed[pid] else 'OPEN'}:{pid}")
                power = (self.bus_voltage_v ** 2 / spec.resistance_ohm * self.applied_duty_pct / 100.0
                         if self.applied_duty_pct > 0 and self.modules[pid] == Presence.ATTACHED and self.switch_closed[pid] else 0.0)
                energy_j[pid] += power * h
                g = spec.ambient_conductance_w_per_c + spec.body_conductance_w_per_c
                equilibrium = (power + spec.ambient_conductance_w_per_c * ambient
                               + spec.body_conductance_w_per_c * cfg.body_temp_c) / g
                # Exact solution for constant power in each short substep.
                self.temperatures[pid] = equilibrium + (self.temperatures[pid] - equilibrium) * math.exp(-g * h / spec.heat_capacity_j_per_c)
        powers = {p: energy_j[p] / dt for p in PANELS}
        actual = sum(powers.values())
        source_avg = ((actual / cfg.power_efficiency + cfg.idle_input_power_w) / self.source_voltage_v
                      if cfg.source_min_voltage_v <= self.source_voltage_v <= cfg.source_max_voltage_v else 0.0)
        return PlantStep(powers, actual, source_avg, tuple(events))
