"""A trained 2 -> 16 -> 8 -> 1 MLP, imitating PI duty requests.

Training uses hypothetical error/integral states, not real garment data.
Inference uses only the standard library; the safety gate remains independent.
"""
import json
import math
from pathlib import Path

from .controller import TemperatureController
from .models import Config, finite

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "pi_imitation.json"


def load_model(path: str | Path | None = None) -> dict:
    model = json.loads(Path(path or DEFAULT_MODEL).read_text(encoding="utf-8"))
    if model.get("architecture") != [2, 16, 8, 1] or model.get("version") != 1:
        raise ValueError("unsupported neural model")
    sizes = model["architecture"]
    for i, layer in enumerate(model["layers"]):
        if len(layer["weights"]) != sizes[i] or any(len(row) != sizes[i + 1] for row in layer["weights"]):
            raise ValueError("invalid neural weight dimensions")
        if len(layer["bias"]) != sizes[i + 1]:
            raise ValueError("invalid neural bias dimensions")
        if not all(finite(x) for row in layer["weights"] for x in row) or not all(finite(x) for x in layer["bias"]):
            raise ValueError("non-finite neural weights")
    if len(model["layers"]) != 3:
        raise ValueError("three neural layers required")
    return model


def predict_duty(model: dict, error_c: float, integral: float) -> float:
    values = [max(-1.0, min(1.0, error_c / 20.0)), 2.0 * integral - 1.0]
    for i, layer in enumerate(model["layers"]):
        raw = [b + sum(values[k] * layer["weights"][k][j] for k in range(len(values)))
               for j, b in enumerate(layer["bias"])]
        values = [math.tanh(x) for x in raw] if i < 2 else [1.0 / (1.0 + math.exp(-max(-60, min(60, x)))) for x in raw]
    return max(0.0, min(1.0, values[0]))


class NeuralController(TemperatureController):
    def __init__(self, config: Config, model_path: str | Path | None = None):
        super().__init__(config)
        self.model = load_model(model_path)
        if abs(self.model["teacher_kp"] - config.kp) > 1e-12 or abs(self.model["teacher_ki"] - config.ki) > 1e-12:
            raise ValueError("neural teacher gains differ from config; retrain with matching gains")

    def request_pct(self, back_temp_c: float | None, running: bool) -> float:
        if not running or not finite(back_temp_c):
            return super().request_pct(back_temp_c, False)
        self._error = self.config.target_temp_c - back_temp_c
        self._request = predict_duty(self.model, self._error, self.integral)
        # Match PI's hard zero/full saturation outside the training envelope.
        if self._error <= -20:
            self._request = 0.0
        elif self._error >= 20:
            self._request = 1.0
        return 100.0 * self._request
