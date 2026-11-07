"""Wall-clock playback of deterministic simulation samples; independent of UI."""
from dataclasses import dataclass


@dataclass
class Playback:
    duration_s: float
    position_s: float = 0.0
    speed: float = 1.0
    playing: bool = False
    last_wall_s: float | None = None

    def tick(self, now: float) -> float:
        if self.playing and self.last_wall_s is not None:
            self.position_s = min(self.duration_s, self.position_s + max(0.0, now - self.last_wall_s) * self.speed)
        self.last_wall_s = now
        if self.position_s >= self.duration_s:
            self.playing = False
        return self.position_s

    def start(self, now: float) -> None:
        self.position_s = 0.0
        self.playing = True
        self.last_wall_s = now

    def pause(self, now: float) -> None:
        self.tick(now)
        self.playing = False

    def resume(self, now: float) -> None:
        if self.position_s < self.duration_s:
            self.last_wall_s = now
            self.playing = True

    def set_speed(self, speed: float, now: float) -> None:
        self.tick(now)
        self.speed = speed

    def finish(self) -> None:
        self.position_s = self.duration_s
        self.playing = False
