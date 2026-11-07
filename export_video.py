"""Export the 10-minute neural-control demo as a 60-second, 10x MP4."""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

import imageio_ffmpeg

from thermallink.models import Config
from thermallink.reporting import csv_text
from thermallink.simulation import run_simulation
from thermallink.video_frames import render_frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/THERMAL_LINK_10x_60s.mp4"))
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    base = Config.load(Path(__file__).parent / "config/simulation.json")
    config = replace(base, ambient_profile="triangle", ambient_min_c=-5.0,
                     ambient_max_c=5.0, ambient_ramp_s=180.0)
    result = run_simulation("normal", 600.0, config, controller_kind="neural")
    poster = output.with_suffix(".png")
    render_frame(result.rows, config, 300.0).save(poster)
    if args.preview_only:
        print(poster, flush=True)
        return

    fps, count = 24, 1440
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    command = [ffmpeg, "-hide_banner", "-y", "-f", "rawvideo", "-vcodec", "rawvideo",
               "-pix_fmt", "rgb24", "-s", "1920x1080", "-r", str(fps), "-i", "-",
               "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
    with output.with_suffix(".encoding.log").open("wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for index in range(count):
                # Endpoints include both 00:00 and 10:00 in exactly 1440 frames.
                simulated_time = 600.0 * index / (count - 1)
                process.stdin.write(render_frame(result.rows, config, simulated_time).tobytes())
                if (index + 1) % 240 == 0:
                    print(f"Encoded {index + 1}/{count} frames", flush=True)
            process.stdin.close()
            code = process.wait()
            if code:
                raise RuntimeError(f"Video encoder exited with {code}; see {log.name}")
        except BaseException:
            process.kill()
            process.wait()
            raise

    # A single metadata read checks duration and format without a full decode.
    probe = imageio_ffmpeg.read_frames(str(output))
    try:
        metadata = next(probe)
    finally:
        probe.close()
    if abs(metadata["duration"] - 60.0) > 0.05 or tuple(metadata["size"]) != (1920, 1080):
        raise RuntimeError(f"Unexpected video format: {metadata}")
    output.with_suffix(".csv").write_text(csv_text(result.rows), encoding="utf-8-sig")
    output.with_suffix(".json").write_text(json.dumps({
        "video": {"duration_s": 60, "fps": fps, "frames": count, "speed": 10,
                  "width": 1920, "height": 1080, "codec": metadata["codec"]},
        "config": asdict(config), "simulation": result.summary(),
        "model_metadata": result.model_metadata,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "duration_s": metadata["duration"],
                      "size": metadata["size"], "bytes": output.stat().st_size}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
