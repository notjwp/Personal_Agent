"""The dwindle layout tree, which is the only hard algorithm in the interface.

Pure, and deliberately runnable with `textual` uninstalled: that is the whole
reason `tiling.py` is a module rather than methods on the workspace screen.
"""
import pathlib
import subprocess
import sys

import pytest

from agent.ui import tiling
from agent.ui.tiling import Leaf, Rect, Split

ROOT = pathlib.Path(__file__).resolve().parent.parent


def three_level():
    """left | (top-right over (bottom-left | bottom-right)).

    Three levels deep and asymmetric on purpose: a focus move that walks the
    TREE instead of the geometry gets a different answer here, which is the
    distinction being tested.
    """
    return Split("v", 0.5,
                 Leaf("left"),
                 Split("h", 0.5,
                       Leaf("tr"),
                       Split("v", 0.5, Leaf("brl"), Leaf("brr"))))


def uneven():
    """A grid whose columns do not line up, at 81x25:

        tl (0,0,30,9)   tr (31,0,50,9)
        bl (0,10,29,15) br (30,10,51,15)

    `br` begins one cell LEFT of `tr`, so the pane nearest to `tl` on the right
    is the one on the other row. Only the perpendicular filter separates them.
    """
    return Split("h", 0.4,
                 Split("v", 0.375, Leaf("tl"), Leaf("tr")),
                 Split("v", 0.3625, Leaf("bl"), Leaf("br")))


# ---------------------------------------------------------------------- rects

def test_rects_sum_exactly_with_the_gaps_counted():
    boxes = tiling.rects(Split("v", 0.5, Leaf("a"), Leaf("b")), 81, 25, gap=1)
    assert boxes["a"].w + 1 + boxes["b"].w == 81
    assert boxes["a"].h == boxes["b"].h == 25


def test_an_odd_remainder_goes_to_the_second_child_rather_than_being_lost():
    boxes = tiling.rects(Split("v", 0.5, Leaf("a"), Leaf("b")), 80, 25, gap=1)
    assert (boxes["a"].w, boxes["b"].w) == (39, 40)
    assert boxes["a"].w + 1 + boxes["b"].w == 80


def test_the_gap_is_between_siblings_and_never_on_the_outer_edge():
    boxes = tiling.rects(three_level(), 81, 25, gap=1)
    assert min(r.x for r in boxes.values()) == 0
    assert min(r.y for r in boxes.values()) == 0
    assert max(r.x + r.w for r in boxes.values()) == 81
    assert max(r.y + r.h for r in boxes.values()) == 25


@pytest.mark.parametrize("width,height", [(81, 25), (80, 24), (120, 40), (57, 19)])
def test_no_two_panes_ever_overlap(width, height):
    boxes = list(tiling.rects(three_level(), width, height, gap=1).values())
    for index, one in enumerate(boxes):
        for other in boxes[index + 1:]:
            assert not (one.x < other.x + other.w and other.x < one.x + one.w
                        and one.y < other.y + other.h and other.y < one.y + one.h)


def test_a_lone_leaf_takes_the_whole_screen():
    assert tiling.rects(Leaf("chat"), 80, 24) == {"chat": Rect(0, 0, 80, 24)}


# ---------------------------------------------------------------------- split

@pytest.mark.parametrize("width,height,expected", [
    (100, 20, "v"),      # five times wider than tall
    (100, 60, "h"),      # past the 2x threshold, so it stacks
    (40, 20, "h"),       # EXACTLY 2x, and "wider than" is strict
    (41, 20, "v"),
])
def test_dwindle_picks_the_orientation_from_the_targets_own_rectangle(
        width, height, expected):
    tree = tiling.split(Leaf("chat"), "chat", "plan", width, height)
    assert tree.orientation == expected


def test_split_replaces_the_target_leaf_and_keeps_everything_else():
    tree = tiling.split(three_level(), "tr", "plan", 120, 40)
    assert set(tiling.leaves(tree)) == {"left", "tr", "brl", "brr", "plan"}
    assert tiling.leaves(three_level()) == ["left", "tr", "brl", "brr"]


def test_splitting_an_unknown_pane_changes_nothing():
    tree = three_level()
    assert tiling.split(tree, "nope", "plan", 120, 40) is tree


@pytest.mark.parametrize("width,height,orientation", [
    (24, 10, "v"),       # 23 usable columns halve to 11, under the 12 minimum
    (80, 6, "h"),        # 5 usable rows halve to 2, under the 3 minimum
])
def test_a_split_that_would_break_the_minimum_is_refused_unchanged(
        width, height, orientation):
    tree = Leaf("chat")
    assert tiling.split(tree, "chat", "plan", width, height, orientation) is tree


@pytest.mark.parametrize("width,height,orientation", [(25, 10, "v"), (80, 7, "h")])
def test_one_cell_more_and_the_same_split_is_allowed(width, height, orientation):
    tree = tiling.split(Leaf("chat"), "chat", "plan", width, height, orientation)
    boxes = tiling.rects(tree, width, height)
    assert len(boxes) == 2
    assert all(r.w >= tiling.MIN_W and r.h >= tiling.MIN_H for r in boxes.values())


# ---------------------------------------------------------------------- close

@pytest.mark.parametrize("closed,survivor", [("plan", "chat"), ("chat", "plan")])
def test_close_promotes_the_surviving_sibling_rather_than_leaving_a_hole(
        closed, survivor):
    # Both sides, because the two are separate branches and only one of them
    # fires per call.
    tree = tiling.close(Split("v", 0.5, Leaf("chat"), Leaf("plan")), closed)
    assert tree == Leaf(survivor)


def test_closing_a_pane_deep_in_the_tree_collapses_only_its_own_split():
    tree = tiling.close(three_level(), "brl")
    assert tiling.leaves(tree) == ["left", "tr", "brr"]
    assert tree.b.b == Leaf("brr")


def test_closing_the_last_leaf_yields_None():
    assert tiling.close(Leaf("chat"), "chat") is None


def test_closing_an_unknown_pane_changes_nothing():
    tree = three_level()
    assert tiling.close(tree, "nope") is tree


# ---------------------------------------------------------------------- focus

def test_focus_is_geometric_not_tree_structural():
    """`brr`'s nearest pane leftward is its tree-sibling here, but `left` is one
    cheap structural hop away and a tree walk would offer it first."""
    assert tiling.focus(three_level(), "brr", "left", 81, 25) == "brl"


def test_focus_crosses_a_subtree_boundary_when_the_geometry_says_to():
    assert tiling.focus(three_level(), "brl", "left", 81, 25) == "left"


def test_focus_only_considers_panes_overlapping_on_the_other_axis():
    """`br` is nearer to `tl` than `tr` is - zero cells away against one - and
    it is the wrong answer, because it shares no row with the pane you are on."""
    boxes = tiling.rects(uneven(), 81, 25)
    assert boxes["br"].x < boxes["tr"].x
    assert tiling.focus(uneven(), "tl", "right", 81, 25) == "tr"


def test_focus_up_and_down_stay_in_the_source_column():
    assert tiling.focus(three_level(), "brr", "up", 81, 25) == "tr"
    assert tiling.focus(three_level(), "brl", "up", 81, 25) == "tr"


@pytest.mark.parametrize("pane,direction", [
    ("left", "left"), ("left", "up"), ("left", "down"),
    ("tr", "up"), ("brr", "right"), ("brl", "down"),
])
def test_focus_at_an_edge_returns_the_source(pane, direction):
    assert tiling.focus(three_level(), pane, direction, 81, 25) == pane


def test_focus_from_an_unknown_pane_returns_it_unchanged():
    assert tiling.focus(three_level(), "nope", "left", 81, 25) == "nope"


def test_a_tie_goes_to_the_pane_nearest_the_top_left():
    """Moving right from `left`, `tr` and `brl` both begin one cell away."""
    tree = three_level()
    boxes = tiling.rects(tree, 81, 25)
    assert boxes["tr"].x == boxes["brl"].x
    assert boxes["tr"].y < boxes["brl"].y
    assert tiling.focus(tree, "left", "right", 81, 25) == "tr"


# --------------------------------------------------------------------- resize

def test_resize_nudges_the_nearest_ancestor_split_of_matching_orientation():
    tree = tiling.resize(Split("v", 0.5, Leaf("a"), Leaf("b")), "a", "right", 0.05)
    assert tree.ratio == pytest.approx(0.55)


def test_resize_walks_PAST_a_split_of_the_wrong_orientation():
    tree = Split("v", 0.5, Leaf("left"), Split("h", 0.5, Leaf("tr"), Leaf("br")))
    out = tiling.resize(tree, "tr", "right", 0.05)
    assert out.ratio == pytest.approx(0.55)
    assert out.b.ratio == pytest.approx(0.5)


@pytest.mark.parametrize("direction,delta,expected", [
    ("right", 0.5, 0.85),
    ("left", 0.5, 0.15),
])
def test_resize_clamps_at_both_ends(direction, delta, expected):
    tree = tiling.resize(Split("v", 0.5, Leaf("a"), Leaf("b")), "a", direction, delta)
    assert tree.ratio == pytest.approx(expected)


def test_resize_with_no_matching_ancestor_changes_nothing():
    tree = Split("v", 0.5, Leaf("a"), Leaf("b"))
    assert tiling.resize(tree, "a", "up", 0.05) is tree


# ------------------------------------------------------------------- the rule

def test_the_module_imports_textual_nowhere():
    source = (ROOT / "agent" / "ui" / "tiling.py").read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines()]
    assert [] == [line for line in lines
                  if line.startswith(("import textual", "from textual"))]


def test_it_imports_with_textual_unavailable():
    """The property that earns the extraction, asserted rather than assumed.

    A later `agent/ui/__init__.py` importing the app eagerly would break this
    and nothing else, so the check blocks textual outright in a subprocess
    rather than looking at sys.modules, which another test may have filled.
    """
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'textual' or name.startswith('textual.'):\n"
        "            raise ImportError('textual is not installed')\n"
        "sys.meta_path.insert(0, Block())\n"
        "from agent.ui import tiling\n"
        "assert tiling.rects(tiling.Leaf('a'), 8, 4)['a'].w == 8\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
