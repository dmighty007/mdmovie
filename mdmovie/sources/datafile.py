"""Read column data files: PLUMED COLVAR, GROMACS .xvg, CSV / whitespace tables."""
from __future__ import annotations

import re

import numpy as np


def read_table(path: str) -> tuple[np.ndarray, list[str]]:
    """Numeric table (rows × columns) and column names (from '#! FIELDS', xvg legends or a header row)."""
    names: list[str] = []
    xvg_legends: dict[int, str] = {}
    xvg_x = ""
    rows = []
    header: list[str] | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#! FIELDS"):                      # PLUMED
                names = s.split()[2:]
                continue
            if s.startswith("@"):                              # GROMACS xvg
                m = re.match(r'@\s+s(\d+)\s+legend\s+"(.*)"', s)
                if m:
                    xvg_legends[int(m.group(1))] = m.group(2)
                m = re.match(r'@\s+xaxis\s+label\s+"(.*)"', s)
                if m:
                    xvg_x = m.group(1)
                continue
            if s[0] in "#;%":
                continue
            parts = [p for p in re.split(r"[,\s;]+", s) if p]
            try:
                rows.append([float(p) for p in parts])
            except ValueError:
                if not rows and header is None:                # CSV header row
                    header = parts
                continue
    if not rows:
        raise ValueError(f"No numeric rows in {path}")
    width = min(len(r) for r in rows)
    table = np.array([r[:width] for r in rows], dtype=float)
    if not names:
        if header:
            names = header
        elif xvg_legends or xvg_x:
            names = [xvg_x or "time"] + [xvg_legends.get(i, f"col {i + 1}") for i in range(width - 1)]
    names = (names + [f"col {i}" for i in range(len(names), width)])[:width]
    return table, names


def parse_columns(text: str, width: int) -> list[int]:
    """'1,2' or '1-3' or '' (= every column but the time column 0)."""
    out = []
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part[1:]:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out or list(range(1, width))
