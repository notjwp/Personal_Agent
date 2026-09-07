"""The dwindle layout tree: where panes are, and which one is next to which.

Pure data and pure functions. No `textual` import, no I/O, no widgets - the
layout is the only hard algorithm in the interface and it is the one part worth
testing without a running app.

`orientation` is the direction of the DIVIDER, not of the flow: "v" is a
vertical divider and puts `a` and `b` side by side, "h" stacks them. Trees are
immutable; every operation returns a new tree, or the same object when it
declines to act.

Zoom is deliberately absent. It belongs to the screen as a single pane id,
because putting it in the tree would force split, close, focus and resize to
each know about a state none of them changes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# A pane smaller than this cannot show its own border, title and one row of
# content, so a split that would produce one is refused rather than drawn.
MIN_W = 12
MIN_H = 3

# Ratios past these leave a pane that exists but cannot be read.
MIN_RATIO = 0.15
MAX_RATIO = 0.85

_HORIZONTAL = ("left", "right")


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Leaf:
    pane_id: str


@dataclass(frozen=True)
class Split:
    orientation: str          # "v" side by side, "h" stacked
    ratio: float              # share of the usable space taken by `a`
    a: "Node"
    b: "Node"


Node = Leaf | Split


def leaves(tree: Node) -> list[str]:
    """Every pane id, left to right and top to bottom."""
    if isinstance(tree, Leaf):
        return [tree.pane_id]
    return leaves(tree.a) + leaves(tree.b)


# ---------------------------------------------------------------- geometry

def rects(tree: Node, width: int, height: int, gap: int = 1) -> dict[str, Rect]:
    """Integer cell rectangles for every pane.

    Gaps sit BETWEEN siblings and never on the outer edge, so the panes plus
    the gaps account for the whole area exactly.
    """
    out: dict[str, Rect] = {}
    _place(tree, Rect(0, 0, max(0, width), max(0, height)), gap, out)
    return out


def _place(node: Node, box: Rect, gap: int, out: dict[str, Rect]) -> None:
    if isinstance(node, Leaf):
        out[node.pane_id] = box
        return
    across = node.orientation == "v"
    span = box.w if across else box.h
    usable = max(0, span - gap)
    # Floor for the first child and the remainder for the second, so rounding
    # error is spent rather than lost and the two always sum to `usable`.
    first = min(usable, max(0, int(usable * node.ratio)))
    second = usable - first
    if across:
        _place(node.a, Rect(box.x, box.y, first, box.h), gap, out)
        _place(node.b, Rect(box.x + first + gap, box.y, second, box.h), gap, out)
    else:
        _place(node.a, Rect(box.x, box.y, box.w, first), gap, out)
        _place(node.b, Rect(box.x, box.y + first + gap, box.w, second), gap, out)


# ------------------------------------------------------------------- split

def split(tree: Node, target_id: str, new_id: str, width: int, height: int,
          orientation: str | None = None, gap: int = 1) -> Node:
    """Replace one leaf with a split of itself and `new_id`.

    `width` and `height` are not decoration: dwindle chooses the orientation
    from the target's CURRENT rectangle, and the minimum-size refusal cannot be
    decided without them either.
    """
    boxes = rects(tree, width, height, gap)
    if target_id not in boxes or new_id in boxes:
        return tree
    box = boxes[target_id]
    if orientation is None:
        # The aspect rule IS dwindle. An alternating counter approximates it and
        # gets the wrong answer the moment a pane is closed out of order.
        orientation = "v" if box.w > 2 * box.h else "h"
    grown = _replace_leaf(tree, target_id,
                          Split(orientation, 0.5, Leaf(target_id), Leaf(new_id)))
    after = rects(grown, width, height, gap)
    if any(after[pane].w < MIN_W or after[pane].h < MIN_H
           for pane in (target_id, new_id)):
        return tree
    return grown


def _replace_leaf(node: Node, target_id: str, sub: Node) -> Node:
    if isinstance(node, Leaf):
        return sub if node.pane_id == target_id else node
    return replace(node,
                   a=_replace_leaf(node.a, target_id, sub),
                   b=_replace_leaf(node.b, target_id, sub))


# ------------------------------------------------------------------- close

def close(tree: Node, target_id: str) -> Node | None:
    """Remove a pane; its sibling takes the whole of their split.

    `None` means the last pane is gone. The caller decides what that means -
    here it is only the absence of a tree.
    """
    if isinstance(tree, Leaf):
        return None if tree.pane_id == target_id else tree
    a = close(tree.a, target_id)
    b = close(tree.b, target_id)
    if a is None:
        return b
    if b is None:
        return a
    if a is tree.a and b is tree.b:
        return tree
    return replace(tree, a=a, b=b)


# ------------------------------------------------------------------- focus

def focus(tree: Node, from_id: str, direction: str,
          width: int, height: int, gap: int = 1) -> str:
    """The pane a directional move lands on, or `from_id` at an edge.

    GEOMETRIC, not structural: a tree walk offers whatever is one hop away in
    the data, which is routinely not what is one cell away on the screen.
    """
    boxes = rects(tree, width, height, gap)
    if from_id not in boxes:
        return from_id
    source = boxes[from_id]
    best, best_key = from_id, None
    for pane, box in boxes.items():
        if pane == from_id:
            continue
        gap_to = _distance(source, box, direction)
        if gap_to is None or not _overlaps(source, box, direction):
            continue
        # A tie goes to the pane nearest the top left, which is what iterating
        # in insertion order gives: `rects` fills `a` before `b`, and `a` is
        # always the upper or left child.
        if best_key is None or gap_to < best_key:
            best, best_key = pane, gap_to
    return best


def _distance(source: Rect, box: Rect, direction: str) -> int | None:
    """Cells between the two edges facing each other, or None if `box` is not
    wholly in that direction."""
    reach = {"left": source.x - (box.x + box.w),
             "right": box.x - (source.x + source.w),
             "up": source.y - (box.y + box.h),
             "down": box.y - (source.y + source.h)}[direction]
    return reach if reach >= 0 else None


def _overlaps(source: Rect, box: Rect, direction: str) -> bool:
    if direction in _HORIZONTAL:
        return source.y < box.y + box.h and box.y < source.y + source.h
    return source.x < box.x + box.w and box.x < source.x + source.w


# ------------------------------------------------------------------ resize

def resize(tree: Node, target_id: str, direction: str, delta: float) -> Node:
    """Move the divider the focused pane hangs from.

    The nearest ANCESTOR of matching orientation, not the immediate parent: a
    pane inside a stacked pair still widens against the vertical divider above
    it, which is what pressing left or right there is asking for.
    """
    want = "v" if direction in _HORIZONTAL else "h"
    path = _path(tree, target_id)
    if path is None:
        return tree
    for node in reversed(path):
        if not isinstance(node, Split) or node.orientation != want:
            continue
        step = delta if direction in ("right", "down") else -delta
        ratio = min(MAX_RATIO, max(MIN_RATIO, node.ratio + step))
        if ratio == node.ratio:
            return tree
        return _replace_node(tree, node, replace(node, ratio=ratio))
    return tree


def _path(node: Node, target_id: str) -> list[Node] | None:
    if isinstance(node, Leaf):
        return [node] if node.pane_id == target_id else None
    for child in (node.a, node.b):
        found = _path(child, target_id)
        if found is not None:
            return [node] + found
    return None


def _replace_node(node: Node, old: Node, new: Node) -> Node:
    if node is old:
        return new
    if isinstance(node, Leaf):
        return node
    return replace(node,
                   a=_replace_node(node.a, old, new),
                   b=_replace_node(node.b, old, new))
