"""Reproducible, synthetic PI imitation training with held-out validation."""
import json
from pathlib import Path

from .neural import DEFAULT_MODEL


def train_model(output_path: str | Path = DEFAULT_MODEL, epochs: int = 160,
                seed: int = 20261107, kp: float = 0.12, ki: float = 0.004) -> dict:
    import numpy as np

    rng = np.random.default_rng(seed)
    # Half the samples cover the full range; half target the unsaturated band.
    def samples(n):
        integral = rng.uniform(0, 1, n)
        error = rng.uniform(-20, 20, n)
        error[:n // 2] = (rng.uniform(-0.15, 1.15, n // 2) - integral[:n // 2]) / kp
        x = np.column_stack((error / 20.0, 2 * integral - 1))
        y = np.clip(kp * error + integral, 0, 1).reshape(-1, 1)
        return x, y

    x, y = samples(8000)
    xv, yv = samples(2000)  # Independent values, never used for gradient updates.
    sizes = [2, 16, 8, 1]
    weights = [rng.normal(0, (2 / (a + b)) ** 0.5, (a, b)) for a, b in zip(sizes, sizes[1:])]
    biases = [np.zeros((1, b)) for b in sizes[1:]]
    params = weights + biases
    first = [np.zeros_like(p) for p in params]
    second = [np.zeros_like(p) for p in params]
    step = 0
    history = []

    def forward(batch):
        h1 = np.tanh(batch @ weights[0] + biases[0])
        h2 = np.tanh(h1 @ weights[1] + biases[1])
        pred = 1 / (1 + np.exp(-np.clip(h2 @ weights[2] + biases[2], -60, 60)))
        return h1, h2, pred

    for epoch in range(epochs):
        order = rng.permutation(len(x))
        for start in range(0, len(x), 256):
            batch = order[start:start + 256]
            xb, yb = x[batch], y[batch]
            h1, h2, pred = forward(xb)
            # Binary cross-entropy also supports soft targets in [0,1].
            d3 = (pred - yb) / len(batch)
            d2 = (d3 @ weights[2].T) * (1 - h2 ** 2)
            d1 = (d2 @ weights[1].T) * (1 - h1 ** 2)
            grads = [xb.T @ d1, h1.T @ d2, h2.T @ d3,
                     d1.sum(axis=0, keepdims=True), d2.sum(axis=0, keepdims=True), d3.sum(axis=0, keepdims=True)]
            step += 1
            for i, (param, grad) in enumerate(zip(params, grads)):
                first[i] = 0.9 * first[i] + 0.1 * grad
                second[i] = 0.999 * second[i] + 0.001 * grad ** 2
                param -= 0.003 * (first[i] / (1 - 0.9 ** step)) / (np.sqrt(second[i] / (1 - 0.999 ** step)) + 1e-8)
        if epoch == 0 or (epoch + 1) % 10 == 0 or epoch == epochs - 1:
            pred = forward(xv)[2]
            history.append({"epoch": epoch + 1, "validation_mae_pct_points": float(np.abs(pred - yv).mean() * 100)})
    error = np.abs(forward(xv)[2] - yv) * 100
    metadata = {
        "description": "Synthetic PI imitation; not real garment data, not an optimized or certified controller",
        "training_samples": len(x), "validation_samples": len(xv), "epochs": epochs, "seed": seed,
        "validation_mae_pct_points": float(error.mean()), "validation_max_error_pct_points": float(error.max()),
        "history": history,
        "inputs": ["BACK temperature error / 20 C", "2 * PI integral state - 1"],
        "output": "Requested PWM ratio, before independent power and safety limits",
        "ambient_role": "Ambient affects the thermal plant and measured BACK error; not a direct neural input",
    }
    model = {"version": 1, "architecture": sizes, "teacher_kp": kp, "teacher_ki": ki,
             "layers": [{"weights": w.tolist(), "bias": b[0].tolist()} for w, b in zip(weights, biases)],
             "metadata": metadata}
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(model, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return metadata
