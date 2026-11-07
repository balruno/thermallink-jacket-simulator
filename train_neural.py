import argparse
import json

from thermallink.neural import DEFAULT_MODEL
from thermallink.training import train_model

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train a small neural network to imitate PI on synthetic states")
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--output", default=str(DEFAULT_MODEL))
    args = parser.parse_args()
    if not 1 <= args.epochs <= 1000:
        parser.error("epochs must be 1..1000")
    print(json.dumps(train_model(args.output, args.epochs), indent=2))
