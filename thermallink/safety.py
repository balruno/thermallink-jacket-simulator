from dataclasses import dataclass

from .models import (Command, Config, Fault, FaultCode, GLOVES, PANELS, PanelId,
                     Presence, SensorError, StepInput, SystemState, finite)
from .power import PowerInput, PowerOutput, PowerState, allocate_power


@dataclass(frozen=True)
class StepOutput:
    timestamp_s: float
    system_state: SystemState
    latched_faults: tuple[Fault, ...]
    active_faults: tuple[Fault, ...]
    controller_duty_pct: float
    power_limited_duty_pct: float
    applied_duty_pct: float
    power: PowerOutput
    estimated_panel_power_w: dict[PanelId, float]
    estimated_power_w: float
    estimated_source_avg_current_a: float
    events: tuple[str, ...]


class SafetyController:
    """Single fault latch and final common-PWM gate. No physical I/O."""

    def __init__(self, config: Config):
        self.config = config
        self.state = SystemState.OFF
        self.latched_faults: tuple[Fault, ...] = ()
        self._power_state = PowerState()
        self._last_time: float | None = None
        self._last_modules: dict[PanelId, Presence] | None = None
        self._stable_since: float | None = None

    def step(self, inp: StepInput) -> StepOutput:
        cfg = self.config
        old_state = self.state
        attached = frozenset(p for p in PANELS if inp.modules.get(p) == Presence.ATTACHED)
        requested = inp.requested_duty_pct / 100.0 if finite(inp.requested_duty_pct) else float("nan")
        power, self._power_state = allocate_power(
            cfg, PowerInput(inp.source_voltage_v, inp.bus_voltage_v, requested, attached), self._power_state)
        if power.status.value == "CONFIG_ERROR" and power.fault and power.fault.code == FaultCode.CONFIG_ERROR:
            self.latched_faults = (power.fault,)
            self.state = SystemState.FAULT_LATCHED
            return StepOutput(inp.timestamp_s, self.state, self.latched_faults, self.latched_faults,
                              0.0, 0.0, 0.0, power, dict.fromkeys(PANELS, 0.0), 0.0, 0.0,
                              ("CONFIG_ERROR: output blocked",))
        faults: list[Fault] = []
        events = list(power.events)
        if power.fault:
            faults.append(power.fault)
        time_ok = finite(inp.timestamp_s) and inp.timestamp_s >= 0
        if not time_ok or (self._last_time is not None and (
                inp.timestamp_s <= self._last_time or inp.timestamp_s - self._last_time > cfg.max_control_gap_s)):
            faults.append(Fault(FaultCode.CONTROL_TIMING, "invalid, repeated, reversed, or delayed control timestamp"))
        if not isinstance(inp.command, Command):
            faults.append(Fault(FaultCode.INPUT_INVALID, "unknown command"))
        if set(inp.modules) != set(PANELS) or not set(inp.sensors) <= set(PANELS):
            faults.append(Fault(FaultCode.INPUT_INVALID, "invalid module or sensor IDs"))

        changed = self._last_modules is not None and inp.modules != self._last_modules
        if self._last_modules is None or changed:
            self._stable_since = inp.timestamp_s if time_ok else None
        stable = (time_ok and self._stable_since is not None
                  and inp.timestamp_s - self._stable_since >= cfg.connection_stable_s)
        release_ok = True
        for pid in PANELS:
            presence = inp.modules.get(pid)
            if not isinstance(presence, Presence) or presence in (Presence.UNKNOWN, Presence.UNSTABLE):
                faults.append(Fault(FaultCode.MODULE_FAULT, f"{pid}: {presence}"))
                release_ok = False
                continue
            if presence == Presence.DETACHED:
                if pid not in GLOVES:
                    faults.append(Fault(FaultCode.MODULE_FAULT, f"{pid}: fixed panel detached"))
                continue  # Detached glove is NOT a sensor-open fault.
            reading = inp.sensors.get(pid)
            if reading is None:
                faults.append(Fault(FaultCode.SENSOR_FAULT, f"{pid}: NO_SAMPLE"))
                release_ok = False
                continue
            error = None
            if not isinstance(reading.error, SensorError) or reading.error != SensorError.NONE or reading.sensor_valid is not True:
                error = str(reading.error) if reading.error != SensorError.NONE else "INVALID"
            elif not finite(reading.temperature_c) or not cfg.sensor_min_c <= reading.temperature_c <= cfg.sensor_max_c:
                error = "RANGE"
            elif not finite(reading.timestamp_s) or not time_ok or not 0 <= inp.timestamp_s - reading.timestamp_s <= cfg.sensor_max_age_s:
                error = "NO_SAMPLE (stale/future timestamp)"
            if error:
                faults.append(Fault(FaultCode.SENSOR_FAULT, f"{pid}: {error}"))
                release_ok = False
            else:
                if reading.temperature_c >= cfg.temp_cutoff_c:
                    faults.append(Fault(FaultCode.OVER_TEMP, f"{pid}: >= {cfg.temp_cutoff_c:g} C"))
                if reading.temperature_c > cfg.temp_release_c:
                    release_ok = False

        if changed and old_state == SystemState.RUNNING:
            self.state = SystemState.RECONNECT_HOLD
            events.append("RECONNECT_HOLD: connection changed; separate START required")
        # Monitor faults in OFF too: OFF suppresses output but never clears a latch.
        for fault in faults:
            if fault not in self.latched_faults:
                self.latched_faults += (fault,)
        if inp.command == Command.OFF:
            self.state = SystemState.OFF
        elif self.latched_faults:
            self.state = SystemState.FAULT_LATCHED
            if inp.command == Command.RESET and not faults and release_ok and stable:
                self.latched_faults = ()
                self.state = SystemState.READY
                events.append("RESET_ACCEPTED: READY; separate START required")
            elif inp.command == Command.RESET:
                events.append("RESET_REJECTED: cause, temperature, or connection not ready")
        elif self.state == SystemState.OFF:
            if inp.command == Command.BOOT:
                self.state = SystemState.SELF_TEST
                self._stable_since = inp.timestamp_s
        elif self.state == SystemState.SELF_TEST:
            if stable:
                self.state = SystemState.READY
                events.append("SELF_TEST_PASSED")
        elif inp.command == Command.STOP and self.state != SystemState.RECONNECT_HOLD:
            self.state = SystemState.READY
        elif inp.command == Command.START and self.state in (SystemState.READY, SystemState.RECONNECT_HOLD):
            if stable:
                self.state = SystemState.RUNNING
            else:
                events.append("START_REJECTED: waiting for stable connection")

        final_duty = power.duty_applied if self.state == SystemState.RUNNING and not self.latched_faults else 0.0
        estimated_panels = {p: power.panel_on_power_w[p] * final_duty for p in PANELS}
        estimated_total = sum(estimated_panels.values())
        # Heater power/current become zero when gated. Source current may include idle electronics.
        source_current = ((estimated_total / cfg.power_efficiency + cfg.idle_input_power_w) / inp.source_voltage_v
                          if not cfg.errors() and finite(inp.source_voltage_v)
                          and cfg.source_min_voltage_v <= inp.source_voltage_v <= cfg.source_max_voltage_v else 0.0)
        if old_state != self.state:
            events.append(f"STATE {old_state} -> {self.state}")
        if time_ok:
            self._last_time = inp.timestamp_s
        self._last_modules = dict(inp.modules)
        return StepOutput(
            timestamp_s=inp.timestamp_s, system_state=self.state,
            latched_faults=self.latched_faults, active_faults=tuple(faults),
            controller_duty_pct=inp.requested_duty_pct if finite(inp.requested_duty_pct) else 0.0,
            power_limited_duty_pct=power.duty_applied * 100.0,
            applied_duty_pct=final_duty * 100.0, power=power,
            estimated_panel_power_w=estimated_panels, estimated_power_w=estimated_total,
            estimated_source_avg_current_a=source_current, events=tuple(events))
