"""Resolution-independent layout tree.

A layout is either a Leaf (one cell, optionally holding a panel id) or a Split
that divides its rectangle among children, side by side ("h") or stacked ("v").
Rectangles are normalized (0..1) so the same layout works at any output size.
Leaves are addressed by their path: the tuple of child indices from the root.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Rect = tuple[float, float, float, float]  # x, y, w, h
MIN_RATIO = 0.05


@dataclass
class Leaf:
    panel: str | None = None

    def to_dict(self) -> dict:
        return {"type": "leaf", "panel": self.panel}


@dataclass
class Split:
    orient: str = "h"  # "h": children left→right, "v": children top→bottom
    ratios: list[float] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"type": "split", "orient": self.orient, "ratios": list(self.ratios),
                "children": [c.to_dict() for c in self.children]}


Node = Leaf | Split


def from_dict(d: dict) -> Node:
    if d.get("type") == "split":
        children = [from_dict(c) for c in d["children"]]
        return Split(d.get("orient", "h"), _normalize(d.get("ratios") or [1] * len(children)), children)
    return Leaf(d.get("panel"))


def _normalize(ratios: list[float]) -> list[float]:
    s = sum(ratios)
    return [r / s for r in ratios] if s > 0 else [1 / len(ratios)] * len(ratios)


def child_rects(split: Split, rect: Rect) -> list[Rect]:
    x, y, w, h = rect
    out, acc = [], 0.0
    for r in split.ratios:
        out.append((x + acc * w, y, r * w, h) if split.orient == "h" else (x, y + acc * h, w, r * h))
        acc += r
    return out


def iter_leaves(node: Node, rect: Rect = (0.0, 0.0, 1.0, 1.0), path: tuple = ()):
    """Yield (path, leaf, rect) for every leaf in drawing order."""
    if isinstance(node, Leaf):
        yield path, node, rect
        return
    for i, (child, r) in enumerate(zip(node.children, child_rects(node, rect))):
        yield from iter_leaves(child, r, path + (i,))


def iter_dividers(node: Node, rect: Rect = (0.0, 0.0, 1.0, 1.0), path: tuple = ()):
    """Yield (split_path, index, split_rect, position) for the divider between child index and index+1.

    `position` is the normalized x (for "h") or y (for "v") coordinate of the divider.
    """
    if isinstance(node, Leaf):
        return
    rects = child_rects(node, rect)
    for i in range(len(node.children) - 1):
        r = rects[i]
        pos = r[0] + r[2] if node.orient == "h" else r[1] + r[3]
        yield path, i, rect, pos
    for i, (child, r) in enumerate(zip(node.children, rects)):
        yield from iter_dividers(child, r, path + (i,))


def get(root: Node, path: tuple) -> Node:
    node = root
    for i in path:
        node = node.children[i]
    return node


def replace(root: Node, path: tuple, new: Node) -> Node:
    if not path:
        return new
    parent = get(root, path[:-1])
    parent.children[path[-1]] = new
    return root


def split_leaf(root: Node, path: tuple, orient: str, new_panel: str | None = None,
               after: bool = True) -> Node:
    """Split a cell in two. Joins the parent split if it already runs in `orient`."""
    leaf = get(root, path)
    new = Leaf(new_panel)
    if path:
        parent = get(root, path[:-1])
        if isinstance(parent, Split) and parent.orient == orient:
            i = path[-1]
            half = parent.ratios[i] / 2
            parent.ratios[i] = half
            j = i + 1 if after else i
            parent.ratios.insert(j, half)
            parent.children.insert(j, new)
            return root
    children = [leaf, new] if after else [new, leaf]
    return replace(root, path, Split(orient, [0.5, 0.5], children))


def remove_leaf(root: Node, path: tuple) -> Node:
    """Remove a cell; its space goes to its siblings. The last cell is only emptied."""
    if not path:
        return Leaf(None)
    parent = get(root, path[:-1])
    i = path[-1]
    del parent.children[i]
    del parent.ratios[i]
    parent.ratios = _normalize(parent.ratios)
    if len(parent.children) == 1:
        root = replace(root, path[:-1], parent.children[0])
    return root


def set_divider(root: Node, split_path: tuple, index: int, pos: float, split_rect: Rect) -> None:
    """Move divider `index` of the split at `split_path` to normalized canvas position `pos`."""
    split = get(root, split_path)
    x, y, w, h = split_rect
    frac = (pos - x) / w if split.orient == "h" else (pos - y) / h
    before = sum(split.ratios[:index])
    pair = split.ratios[index] + split.ratios[index + 1]
    left = min(max(frac - before, MIN_RATIO), pair - MIN_RATIO)
    split.ratios[index] = left
    split.ratios[index + 1] = pair - left


def find_panel(root: Node, panel_id: str) -> tuple | None:
    for path, leaf, _ in iter_leaves(root):
        if leaf.panel == panel_id:
            return path
    return None


def panel_ids(root: Node) -> list[str]:
    return [leaf.panel for _, leaf, _ in iter_leaves(root) if leaf.panel]


def _grid(rows: int, cols: int, ids: list) -> Node:
    it = iter(ids)
    row_nodes = [Split("h", [1 / cols] * cols, [Leaf(next(it, None)) for _ in range(cols)])
                 for _ in range(rows)]
    return row_nodes[0] if rows == 1 else Split("v", [1 / rows] * rows, row_nodes)


TEMPLATES = {
    "Single": lambda ids: Leaf(ids[0] if ids else None),
    "Side by side (1×2)": lambda ids: _grid(1, 2, ids),
    "Stacked (2×1)": lambda ids: Split("v", [0.5, 0.5], [Leaf(i) for i in (ids + [None, None])[:2]]),
    "Grid 2×2": lambda ids: _grid(2, 2, ids),
    "Three in a row (1×3)": lambda ids: _grid(1, 3, ids),
    "Big left + 2 right": lambda ids: Split("h", [0.6, 0.4], [
        Leaf((ids + [None])[0]),
        Split("v", [0.5, 0.5], [Leaf(i) for i in (ids + [None] * 3)[1:3]])]),
    "Big top + strip bottom": lambda ids: Split("v", [0.72, 0.28], [
        Leaf((ids + [None])[0]), Leaf((ids + [None, None])[1])]),
}


def apply_template(name: str, current: Node) -> Node:
    """Build a template layout, re-using the panels currently placed (in order)."""
    return TEMPLATES[name](panel_ids(current))
