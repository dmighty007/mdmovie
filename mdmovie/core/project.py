"""The project: canvas settings, layout, panels, overlays, sync groups and trajectories."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from mdmovie.core import layout as L
from mdmovie.core.timemap import ResolvedMap, SyncGroup
from mdmovie.panels import PANEL_TYPES, Panel
from mdmovie.sources.trajectory import TrajectorySource

FILE_SUFFIX = ".mdmovie.json"
RESOLUTION_PRESETS = {
    "1920×1080 (Full HD)": (1920, 1080),
    "1280×720 (HD)": (1280, 720),
    "3840×2160 (4K)": (3840, 2160),
    "1080×1080 (square)": (1080, 1080),
    "1080×1920 (portrait)": (1080, 1920),
    "2560×1440 (QHD)": (2560, 1440),
}


@dataclass
class Overlay:
    panel: str
    x: float = 0.02
    y: float = 0.02
    w: float = 0.25
    h: float = 0.08

    @property
    def rect(self):
        return (self.x, self.y, self.w, self.h)


PATH_KINDS = ("dir", "file", "image")


def _map_paths(panel, props: dict, series, fn) -> None:
    """Apply fn (make relative / resolve) to every path-valued property and data-file parameter."""
    from mdmovie.analysis.registry import PRESETS
    for prop in type(panel).all_props():
        if prop.kind in PATH_KINDS and props.get(prop.name):
            props[prop.name] = fn(props[prop.name])
    for s in series:
        params = s["params"] if isinstance(s, dict) else s.params
        name = s["preset"] if isinstance(s, dict) else s.preset
        pdef = PRESETS.get(name)
        for prm in (pdef.params if pdef else []):
            if prm.kind == "file" and params.get(prm.name):
                params[prm.name] = fn(params[prm.name])


def _even(n) -> int:
    n = max(2, int(n))
    return n + (n % 2)


class Project:
    VERSION = 1

    def __init__(self):
        self.path: str | None = None
        self.width = 1920
        self.height = 1080
        self.fps = 30.0
        self.background = "#ffffff"
        self.gutter = 12
        self.n_frames_override = 0          # 0 = auto (longest sync group)
        self.layout: L.Node = L.Split("h", [0.5, 0.5], [L.Leaf(), L.Leaf()])
        self.overlays: list[Overlay] = []
        self.panels: dict[str, Panel] = {}
        self.groups: dict[str, SyncGroup] = {"g1": SyncGroup("g1", "Main")}
        self.trajectories: dict[str, TrajectorySource] = {}

    # --- paths ------------------------------------------------------------------------------
    @property
    def base_dir(self) -> str:
        return os.path.dirname(os.path.abspath(self.path)) if self.path else os.getcwd()

    def resolve_path(self, p: str) -> str:
        if not p:
            return p
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base_dir, p))

    def rel_path(self, p: str) -> str:
        """Paths are stored relative to the project file when it is saved nearby."""
        if not p or not self.path:
            return p
        try:
            rel = os.path.relpath(os.path.abspath(p), self.base_dir)
        except ValueError:
            return p
        return p if rel.startswith(".." + os.sep + ".." + os.sep + "..") else rel

    # --- ids and objects --------------------------------------------------------------------
    def new_id(self, prefix: str) -> str:
        taken = set(self.panels) | set(self.groups) | set(self.trajectories)
        i = 1
        while f"{prefix}{i}" in taken:
            i += 1
        return f"{prefix}{i}"

    def add_panel(self, kind: str, name: str = "", group: str | None = None) -> Panel:
        cls = PANEL_TYPES[kind]
        pid = self.new_id(kind)
        n = sum(1 for p in self.panels.values() if p.KIND == kind) + 1
        panel = cls(pid, name or f"{cls.TITLE} {n}", group or next(iter(self.groups)))
        self.panels[pid] = panel
        return panel

    def remove_panel(self, pid: str) -> None:
        self.panels.pop(pid, None)
        for _, leaf, _ in L.iter_leaves(self.layout):
            if leaf.panel == pid:
                leaf.panel = None
        self.overlays = [o for o in self.overlays if o.panel != pid]

    def add_group(self, name: str = "") -> SyncGroup:
        gid = self.new_id("g")
        colors = ["#4c8bf5", "#e8710a", "#34a853", "#a142f4", "#ea4335", "#12b5cb", "#f9ab00"]
        g = SyncGroup(gid, name or f"Group {len(self.groups) + 1}", color=colors[len(self.groups) % len(colors)])
        self.groups[gid] = g
        return g

    def remove_group(self, gid: str) -> None:
        if len(self.groups) <= 1 or gid not in self.groups:
            return
        del self.groups[gid]
        fallback = next(iter(self.groups))
        for p in self.panels.values():
            if p.group == gid:
                p.group = fallback

    def add_trajectory(self, topology: str, trajectories: list[str], name: str = "") -> TrajectorySource:
        tid = self.new_id("traj")
        t = TrajectorySource(tid, name or os.path.splitext(os.path.basename(topology))[0], topology,
                             list(trajectories))
        self.trajectories[tid] = t
        return t

    def placed_panels(self) -> set[str]:
        return set(L.panel_ids(self.layout)) | {o.panel for o in self.overlays}

    # --- time -------------------------------------------------------------------------------
    def group_members(self, gid: str) -> list[Panel]:
        return [p for p in self.panels.values() if p.group == gid]

    def group_auto(self, gid: str):
        lo = hi = dt = None
        for p in self.group_members(gid):
            info = p.time_info(self)
            if not info:
                continue
            a, b, d = info
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
            dt = d if dt is None else min(dt, d)
        return None if lo is None else (lo, hi, dt)

    def group_map(self, gid: str) -> ResolvedMap:
        g = self.groups.get(gid) or next(iter(self.groups.values()))
        return g.resolve(self.group_auto(g.id))

    def group_time(self, gid: str, gframe: int) -> float | None:
        return self.group_map(gid).source_time(gframe)

    @property
    def n_frames(self) -> int:
        if self.n_frames_override > 0:
            return int(self.n_frames_override)
        ends = [self.group_map(gid).end for gid in self.groups
                if self.group_auto(gid) is not None and any(p.id in self.placed_panels()
                                                            for p in self.group_members(gid))]
        return max(ends, default=1)

    @property
    def size(self) -> tuple[int, int]:
        return _even(self.width), _even(self.height)

    # --- serialisation ----------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": self.VERSION,
            "canvas": {"width": self.width, "height": self.height, "fps": self.fps,
                       "background": self.background, "gutter": self.gutter,
                       "n_frames": self.n_frames_override},
            "layout": self.layout.to_dict(),
            "overlays": [asdict(o) for o in self.overlays],
            "panels": [self._panel_dict(p) for p in self.panels.values()],
            "groups": [g.to_dict() for g in self.groups.values()],
            "trajectories": [t.to_dict(self.rel_path) for t in self.trajectories.values()],
        }

    def _panel_dict(self, p: Panel) -> dict:
        d = p.to_dict(self.rel_path)
        _map_paths(p, d["props"], d.get("series", []), self.rel_path)
        return d

    @classmethod
    def from_dict(cls, d: dict, path: str | None = None) -> "Project":
        pr = cls()
        pr.path = path
        c = d.get("canvas", {})
        pr.width = c.get("width", 1920)
        pr.height = c.get("height", 1080)
        pr.fps = c.get("fps", 30.0)
        pr.background = c.get("background", "#ffffff")
        pr.gutter = c.get("gutter", 12)
        pr.n_frames_override = c.get("n_frames", 0)
        pr.layout = L.from_dict(d.get("layout", {"type": "leaf"}))
        pr.overlays = [Overlay(**o) for o in d.get("overlays", [])]
        pr.groups = {g["id"]: SyncGroup.from_dict(g) for g in d.get("groups", [])} or {
            "g1": SyncGroup("g1", "Main")}
        pr.trajectories = {t["id"]: TrajectorySource.from_dict(t) for t in d.get("trajectories", [])}
        for t in pr.trajectories.values():  # in memory, paths are always absolute
            t.topology = pr.resolve_path(t.topology)
            t.trajectories = [pr.resolve_path(x) for x in t.trajectories]
        pr.panels = {}
        for pd in d.get("panels", []):
            cls_ = PANEL_TYPES.get(pd.get("kind"))
            if cls_ is None:
                continue
            panel = cls_(pd["id"], pd.get("name", ""), pd.get("group", ""), pd.get("props"))
            panel.load_extra(pd, pr.resolve_path)
            _map_paths(panel, panel.props, panel.series, pr.resolve_path)  # in memory, paths are absolute
            if panel.group not in pr.groups:
                panel.group = next(iter(pr.groups))
            pr.panels[panel.id] = panel
        return pr

    def clone(self) -> "Project":
        """Independent copy (fresh panel renderers), e.g. for exporting from a worker thread."""
        return Project.from_dict(json.loads(json.dumps(self.to_dict())), self.path)

    def save(self, path: str) -> None:
        self.path = os.path.abspath(path)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path) as f:
            return cls.from_dict(json.load(f), os.path.abspath(path))
