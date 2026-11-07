"""Continuous linear ambient ramps: -5 -> +5 -> -5 in 180 s per leg."""
from .models import Config


def ambient_temperature(config: Config, time_s: float) -> float:
    if config.ambient_profile == "constant":
        return config.ambient_temp_c
    phase = (time_s % (2.0 * config.ambient_ramp_s)) / config.ambient_ramp_s
    fraction = phase if phase <= 1.0 else 2.0 - phase
    return config.ambient_min_c + (config.ambient_max_c - config.ambient_min_c) * fraction
