"""Stateless power limiter; the system supervisor is the ONLY fault-latch owner."""
from dataclasses import dataclass, field

from .models import Config, Fault, FaultCode, PANELS, PanelId, PowerStatus, finite, zero_panels


@dataclass(frozen=True)
class PowerInput:
    source_voltage_v: float
    bus_voltage_v: float
    requested_duty: float
    attached: frozenset[PanelId]


@dataclass(frozen=True)
class PowerState:
    attached: frozenset[PanelId] | None = None
    # Intentionally no latched_fault: owned exclusively by SafetyController.


@dataclass(frozen=True)
class PowerOutput:
    status: PowerStatus
    fault: Fault | None = None
    duty_requested: float = 0.0
    duty_applied: float = 0.0  # Limiter output, NOT final hardware PWM.
    power_budget_w: float = 0.0
    power_requested_w: float = 0.0
    power_actual_w: float = 0.0  # Candidate estimate, before the safety gate.
    bus_on_current_a: float = 0.0
    source_on_current_a: float = 0.0
    source_avg_current_a: float = 0.0
    panel_on_power_w: dict[PanelId, float] = field(default_factory=zero_panels)
    panel_power_w: dict[PanelId, float] = field(default_factory=zero_panels)
    panel_current_avg_a: dict[PanelId, float] = field(default_factory=zero_panels)
    panel_share: dict[PanelId, float] = field(default_factory=zero_panels)
    events: tuple[str, ...] = ()

    @property
    def fault_code(self) -> str:
        return self.fault.code.value if self.fault else "NONE"


def allocate_power(config: Config, step_input: PowerInput,
                   previous_state: PowerState | None = None) -> tuple[PowerOutput, PowerState]:
    """allocate_power(config, input, previous_state) -> (output, next_state).

    Efficiency maps source heater-branch input power to bus output power once.
    Idle power is on the SOURCE side. The unknown thermostats are all assumed
    closed for both current screening and the conservative average budget.
    """
    inp = step_input
    state = PowerState(inp.attached)
    errors = config.errors()
    if errors:
        return PowerOutput(PowerStatus.CONFIG_ERROR, Fault(FaultCode.CONFIG_ERROR, "; ".join(errors))), state
    if (not finite(inp.requested_duty) or not 0 <= inp.requested_duty <= 1
            or not isinstance(inp.attached, frozenset) or not inp.attached <= set(PANELS)):
        return PowerOutput(PowerStatus.CONFIG_ERROR, Fault(FaultCode.INPUT_INVALID, "invalid duty or attached panel IDs")), state
    if (not finite(inp.source_voltage_v) or not finite(inp.bus_voltage_v)
            or not config.source_min_voltage_v <= inp.source_voltage_v <= config.source_max_voltage_v
            or not config.bus_min_voltage_v <= inp.bus_voltage_v <= config.bus_max_voltage_v):
        return PowerOutput(PowerStatus.NO_POWER, Fault(FaultCode.VOLTAGE_FAULT, "source or bus voltage out of range")), state
    events = ()
    if previous_state is not None and previous_state.attached is not None and previous_state.attached != inp.attached:
        events = ("CONNECTED_SET_CHANGED",)
    try:
        on = {p: inp.bus_voltage_v ** 2 / config.panel_specs[p].resistance_ohm if p in inp.attached else 0.0 for p in PANELS}
        total = sum(on.values())
        source_budget = min(config.source_cont_power_w, inp.source_voltage_v * config.source_cont_current_a)
        budget = min(config.heater_max_avg_power_w,
                     config.power_efficiency * max(0.0, source_budget - config.idle_input_power_w))
        bus_current = total / inp.bus_voltage_v
        source_on = (total / config.power_efficiency + config.idle_input_power_w) / inp.source_voltage_v
        if not all(finite(v) for v in (*on.values(), total, source_budget, budget, bus_current, source_on)):
            raise OverflowError("non-finite derived electrical value")
    except (OverflowError, ZeroDivisionError):
        return PowerOutput(PowerStatus.CONFIG_ERROR, Fault(FaultCode.CONFIG_ERROR, "electrical calculation overflow")), state
    fault = None
    if bus_current > config.bus_peak_current_a or source_on > config.source_peak_current_a:
        fault = Fault(FaultCode.PEAK_LIMIT, f"bus_on={bus_current:.3f} A; source_on={source_on:.3f} A")
        duty, status = 0.0, PowerStatus.PEAK_LIMIT
    elif total == 0 or budget <= 0:
        duty, status = 0.0, PowerStatus.NO_POWER
    else:
        duty = min(inp.requested_duty, budget / total, 1.0)
        status = (PowerStatus.IDLE if inp.requested_duty == 0 else
                  PowerStatus.POWER_LIMITED if duty < inp.requested_duty - 1e-12 else PowerStatus.NORMAL)
    powers = {p: on[p] * duty for p in PANELS}
    actual = sum(powers.values())
    return PowerOutput(
        status=status, fault=fault, duty_requested=inp.requested_duty, duty_applied=duty,
        power_budget_w=budget, power_requested_w=total * inp.requested_duty,
        power_actual_w=actual, bus_on_current_a=bus_current, source_on_current_a=source_on,
        source_avg_current_a=(actual / config.power_efficiency + config.idle_input_power_w) / inp.source_voltage_v,
        panel_on_power_w=on, panel_power_w=powers,
        panel_current_avg_a={p: powers[p] / inp.bus_voltage_v for p in PANELS},
        panel_share={p: powers[p] / actual if actual else 0.0 for p in PANELS}, events=events,
    ), state
