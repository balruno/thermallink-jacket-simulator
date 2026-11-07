"""Transparent PI reference controller using BACK only; no trained AI model."""
from .models import Config, finite


class TemperatureController:
    def __init__(self, config: Config):
        self.config = config
        self.integral = 0.0
        self._error = 0.0
        self._request = 0.0

    def request_pct(self, back_temp_c: float | None, running: bool) -> float:
        if not running or not finite(back_temp_c):
            self.integral = 0.0
            self._error = 0.0
            self._request = 0.0
            return 0.0
        self._error = self.config.target_temp_c - back_temp_c
        self._request = max(0.0, min(1.0, self.config.kp * self._error + self.integral))
        return 100.0 * self._request

    def feedback(self, applied_duty_pct: float, dt: float, running: bool) -> None:
        if not running:
            self.integral = 0.0
            return
        # Conditional integration prevents windup at power/safety saturation.
        saturated_high = self._request >= 1.0 or applied_duty_pct / 100.0 < self._request - 1e-9
        saturated_low = self._request <= 0.0
        if not ((saturated_high and self._error > 0) or (saturated_low and self._error < 0)):
            self.integral = max(0.0, min(1.0, self.integral + self.config.ki * self._error * dt))
