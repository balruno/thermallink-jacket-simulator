"""Portable logical contract only. No ESP32 pins/ADC/watchdog/serial driver."""
import json
from typing import Protocol

from .models import Command, PanelId, Presence, SensorError, SensorReading, StepInput
from .safety import StepOutput


class HardwareAdapter(Protocol):
    def read_inputs(self, timestamp_s: float, requested_duty_pct: float,
                    command: Command = Command.NONE) -> StepInput: ...
    def write_output(self, output: StepOutput) -> None: ...


def encode_input(inp: StepInput) -> str:
    return json.dumps({
        "contract": "TL-REVC-5P-1PWM-v1", "timestamp_s": inp.timestamp_s,
        "source_voltage_v": inp.source_voltage_v, "bus_voltage_v": inp.bus_voltage_v,
        "requested_duty_pct": inp.requested_duty_pct, "command": inp.command.value,
        "modules": {p.value: v.value for p, v in inp.modules.items()},
        "sensors": {p.value: {"temperature_c": s.temperature_c, "timestamp_s": s.timestamp_s,
                              "sensor_valid": s.sensor_valid, "error": s.error.value}
                    for p, s in inp.sensors.items()},
    }, allow_nan=False)


def decode_input(frame: str) -> StepInput:
    raw = json.loads(frame, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"non-finite JSON: {value}")))
    if raw.get("contract") != "TL-REVC-5P-1PWM-v1":
        raise ValueError("unsupported interface contract")
    return StepInput(
        timestamp_s=raw["timestamp_s"], source_voltage_v=raw["source_voltage_v"],
        bus_voltage_v=raw["bus_voltage_v"], requested_duty_pct=raw["requested_duty_pct"],
        command=Command(raw["command"]), modules={PanelId(k): Presence(v) for k, v in raw["modules"].items()},
        sensors={PanelId(k): SensorReading(v["temperature_c"], v["timestamp_s"], v["sensor_valid"], SensorError(v["error"]))
                 for k, v in raw["sensors"].items()})


def encode_output(output: StepOutput) -> str:
    return json.dumps({"contract": "TL-REVC-5P-1PWM-v1", "timestamp_s": output.timestamp_s,
                       "system_state": output.system_state.value,
                       "applied_duty_pct": output.applied_duty_pct,
                       "faults": [f.code.value for f in output.latched_faults]}, allow_nan=False)


class ESP32Adapter:
    """Explicit porting placeholder; H15 wire-format tests are not hardware proof."""
    def read_inputs(self, timestamp_s: float, requested_duty_pct: float,
                    command: Command = Command.NONE) -> StepInput:
        raise NotImplementedError("ESP32 ADC/presence/voltage acquisition has not been implemented or validated")

    def write_output(self, output: StepOutput) -> None:
        raise NotImplementedError("ESP32 physical PWM and independent watchdog have not been implemented or validated")
