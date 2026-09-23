"""Sync model: map the global movie clock onto simulation time.

Every panel belongs to a SyncGroup. Panels in the same group see the same
simulation time at every movie frame (they are synchronised); putting a panel
in its own group makes it independent. Time is measured in picoseconds, so an
image sequence and a trajectory line up physically even when their strides
differ.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

EDGE_MODES = ("hold", "hide", "loop")


@dataclass
class SyncGroup:
    id: str
    name: str = "Group"
    start: int = 0                  # movie frame at which src_start is shown
    speed: float = 0.0              # ps per movie frame; <= 0 means "auto" (one source frame per movie frame)
    src_start: float | None = None  # ps; None means earliest time of the group's members
    src_end: float | None = None    # ps; None means latest time of the group's members
    before: str = "hold"            # what to show before `start`: hold | hide | loop
    after: str = "hold"             # what to show after the end: hold | hide | loop
    color: str = "#4c8bf5"

    def resolve(self, auto: tuple[float, float, float] | None) -> "ResolvedMap":
        """Combine the user's settings with the automatic (lo, hi, dt) of the members."""
        lo, hi, dt = auto if auto else (0.0, 0.0, 1.0)
        if self.src_start is not None:
            lo = float(self.src_start)
        if self.src_end is not None:
            hi = float(self.src_end)
        if hi < lo:
            hi = lo
        speed = float(self.speed) if self.speed and self.speed > 0 else (dt if dt > 0 else 1.0)
        return ResolvedMap(int(self.start), lo, hi, speed, self.before, self.after)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SyncGroup":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ResolvedMap:
    start: int
    lo: float
    hi: float
    speed: float
    before: str = "hold"
    after: str = "hold"

    @property
    def n_frames(self) -> int:
        return int(math.floor((self.hi - self.lo) / self.speed + 1e-6)) + 1

    @property
    def end(self) -> int:
        """First movie frame after the group has finished playing (exclusive)."""
        return self.start + self.n_frames

    def source_time(self, gframe: int) -> float | None:
        """Simulation time (ps) shown at movie frame `gframe`, or None if hidden."""
        local = gframe - self.start
        n = self.n_frames
        if local < 0:
            if self.before == "hide":
                return None
            local = local % n if self.before == "loop" else 0
        elif local >= n:
            if self.after == "hide":
                return None
            local = local % n if self.after == "loop" else n - 1
        return min(self.lo + local * self.speed, self.hi)
