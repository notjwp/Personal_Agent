"""The landing screen, the tiled workspace, and the shim that keeps --tui.

Folded into the tenth deviation with the rest of the interface. What is here
and not in test_tui.py is what test_tui.py was never about: the wordmark, the
layout rules, the keymap, and the landing's refusal to claim a condition
nobody checked.
"""
import asyncio
import os

import pytest
from textual.widgets import Input, Static

from agent import config
from agent.ui import panes, screens, theme, tiling


class Values:
    def __init__(self, values):
        self.values = values


class FakeGraph:
    def __init__(self, values=None):
        self._values = values or {}

    def get_state(self, cfg):
        return Values(dict(self._values))

    def invoke(self, payload, cfg):
        return {}


def drive(app, script, size=(110, 32)):
    async def _go():
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            await script(pilot)
    asyncio.run(_go())


def rendered(app) -> str:
    return "\n".join(str(strip.text)
                     for strip in app.screen._compositor.render_strips())


# ================================================================ the wordmark

def test_all_six_rows_are_exactly_46_cells():
    """The failure mode of every ASCII logotype is a ragged row, and it is
    invisible in a diff."""
    from rich.cells import cell_len

    assert len(screens.LOGO) == 6
    for row in screens.LOGO:
        assert len(row) == 46, repr(row)
        assert cell_len(row) == screens.LOGO_WIDTH, repr(row)


def test_the_wordmark_is_the_artwork_and_not_a_regeneration():
    """A golden test. Deliberately NOT a "no double space" check: E's short arm
    and N's diagonal contain doubled spaces legitimately, and such a check
    fails on correct art."""
    assert screens.LOGO == (
        "███╗   ██╗ ██████╗ ███████╗███████╗██╗███████╗",
        "████╗  ██║██╔═══██╗██╔════╝██╔════╝██║██╔════╝",
        "██╔██╗ ██║██║   ██║█████╗  ███████╗██║███████╗",
        "██║╚██╗██║██║   ██║██╔══╝  ╚════██║██║╚════██║",
        "██║ ╚████║╚██████╔╝███████╗███████║██║███████║",
        "╚═╝  ╚═══╝ ╚═════╝ ╚══════╝╚══════╝╚═╝╚══════╝",
    )


def test_a_narrow_terminal_gets_the_letterspaced_form_instead():
    assert "N O E S I S" in screens.logotype(40).plain
    assert "███" in screens.logotype(80).plain


def test_the_wordmark_is_one_colour_and_never_a_fade():
    """The accent means focus everywhere else, so a gradient here would make it
    mean two things."""
    art = screens.logotype(80)
    assert {span.style for span in art.spans} == {"row--logo"}


# ================================================================== the landing

def test_the_landing_shows_the_wordmark_and_every_command():
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        text = rendered(app)
        assert "██████╗" in text
        for name in screens.COMMANDS:
            assert name in text, name

    drive(app, script)


def test_a_count_that_has_not_arrived_renders_as_a_placeholder():
    """Section 12.1: nothing touches SQLite on the render path. The landing is
    drawn first and the counts arrive after, so the render path must have
    something to show while they have not."""
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        await app.screen.workers.wait_for_complete()
        app.screen.counts = {}
        app.screen.paint_list()
        await pilot.pause()
        assert "·" in rendered(app)

    drive(app, script)


def test_an_empty_queue_is_not_the_same_as_a_count_nobody_asked_for():
    """`·` means "not loaded yet". A queue with nothing in it is a different
    statement, and rendering both the same way says neither."""
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        await app.screen.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen.counts.get("/tasks") == ""

    drive(app, script)


def test_the_landing_omits_egress_when_nothing_set_it(monkeypatch):
    """A default that asserts the safe answer is how a row comes to claim a
    condition nobody checked. AGENT_EGRESS unset means the field is ABSENT,
    not `unknown`."""
    monkeypatch.delenv("AGENT_EGRESS", raising=False)
    monkeypatch.setenv("AGENT_API_KEY", "nvapi-x" * 4)
    line = screens.status_line().plain
    assert "egress" not in line
    assert "unknown" not in line


def test_the_landing_prints_egress_only_when_it_was_actually_set(monkeypatch):
    monkeypatch.setenv("AGENT_EGRESS", "restricted")
    monkeypatch.setenv("AGENT_API_KEY", "nvapi-x" * 4)
    assert "restricted egress" in screens.status_line().plain


def test_no_api_key_replaces_the_whole_line(monkeypatch):
    for name in ("AGENT_API_KEY", "OPENAI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_PROVIDER", "nvidia")
    line = screens.status_line()
    assert "no API key" in line.plain and "/setup" in line.plain
    assert line.style == "row--denied"


def test_the_model_shown_is_the_one_actually_resolved(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "nvapi-x" * 4)
    monkeypatch.setattr(config, "PROVIDER", "anthropic")
    monkeypatch.setattr(config, "MODEL", "claude-haiku-4-5-20251001")
    assert "claude-haiku-4-5-20251001" in screens.status_line().plain


def test_typing_filters_the_list_and_the_selection_snaps_to_the_first_match():
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        app.screen.index = 3
        app.screen.query_one(Input).value = "/ta"
        await pilot.pause()
        assert app.screen.matches() == ["/tasks"]
        assert app.screen.index == 0

    drive(app, script)


def test_a_goal_typed_on_the_landing_opens_a_workspace():
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        app.screen.query_one(Input).value = "fix the failing test"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, screens.WorkspaceScreen)
        assert app.screen._goal == "fix the failing test"

    drive(app, script)


def test_a_command_on_the_landing_opens_the_workspace_with_its_pane():
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        app.screen.query_one(Input).value = "/tasks"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        assert "tasks" in tiling.leaves(app.screen.tiles)

    drive(app, script)


def test_a_mistyped_command_is_not_sent_as_a_goal():
    """`/taks` is a typo. Sending it to the model would bill a turn for it and
    lose the correction."""
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        app.screen.query_one(Input).value = "/taks"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, screens.LandingScreen)

    drive(app, script)


def test_a_sentence_beginning_with_a_slash_is_still_a_goal():
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        app.screen.query_one(Input).value = "/usr/bin/python is missing"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, screens.WorkspaceScreen)

    drive(app, script)


@pytest.mark.parametrize("height,hint,status", [
    (32, True, True), (19, False, True), (17, False, False)])
def test_the_landing_sheds_rows_before_it_scrolls(height, hint, status):
    """The hint goes first, then the status line. The command list is the one
    thing that must never be clipped."""
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        assert app.screen.query_one("#landing-hint").display is hint
        assert app.screen.query_one("#landing-status").display is status
        for name in screens.COMMANDS:
            assert name in rendered(app), name

    drive(app, script, size=(110, height))


# ================================================================ the workspace

def workspace(values=None, **kw):
    return screens.NoesisApp(FakeGraph(values), thread="w0rk", **kw)


def test_the_workspace_opens_on_chat_alone():
    app = workspace()

    async def script(pilot):
        assert tiling.leaves(app.screen.tiles) == ["chat"]

    drive(app, script)


def test_alt_enter_splits_and_alt_w_closes():
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        assert len(tiling.leaves(app.screen.tiles)) == 2
        await pilot.press("alt+w")
        await pilot.pause()
        assert tiling.leaves(app.screen.tiles) == ["chat"]

    drive(app, script)


def test_chat_cannot_be_closed():
    """Section 8. Closing the conversation would leave a workspace with no way
    to say anything."""
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        app.screen.focus_id = "chat"
        await pilot.press("alt+w")
        await pilot.pause()
        assert "chat" in tiling.leaves(app.screen.tiles)

    drive(app, script)


def test_alt_z_zooms_one_pane_and_escape_restores_the_layout():
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        before = app.screen.tiles
        await pilot.press("alt+z")
        await pilot.pause()
        assert app.screen.zoomed == app.screen.focus_id
        assert app.screen.showing() == [app.screen.focus_id]
        # Zoom is NOT in the tree, which is why the tree survives it untouched.
        assert app.screen.tiles is before
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.zoomed is None
        assert len(app.screen.showing()) == 2

    drive(app, script)


def test_alt_hjkl_moves_focus_geometrically():
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        opened = app.screen.focus_id
        await pilot.press("alt+h")
        await pilot.pause()
        assert app.screen.focus_id == "chat"
        await pilot.press("alt+l")
        await pilot.pause()
        assert app.screen.focus_id == opened

    drive(app, script)


def test_a_resize_changes_the_ratio_and_not_the_panes():
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        before = tiling.leaves(app.screen.tiles)
        await pilot.press("alt+L")
        await pilot.pause()
        assert app.screen.tiles.ratio != pytest.approx(0.5)
        assert tiling.leaves(app.screen.tiles) == before

    drive(app, script)


def test_streaming_never_rebuilds_the_layout():
    """Section 12.5. A rebuild per token is the difference between a terminal
    that streams and one that stutters."""
    app = workspace()

    async def script(pilot):
        before = app.screen.rebuilds
        for _ in range(200):
            app.screen.on_text("token ")
        await pilot.pause()
        assert app.screen.rebuilds == before

    drive(app, script)


def test_a_rebuilt_pane_replays_from_the_screen_not_from_itself():
    """Panes are disposable views. If a rebuilt one came back empty, every
    split would silently eat the conversation."""
    app = workspace()

    async def script(pilot):
        app.screen.note("something worth keeping")
        await pilot.pause()
        await pilot.press("alt+enter")
        await pilot.pause()
        await pilot.pause()
        log = app.screen.query_one("#chat-log")
        assert any("something worth keeping" in strip.text for strip in log.lines)

    drive(app, script)


@pytest.mark.parametrize("values,expected", [
    ({}, "no plan"),
    ({"phase": "planning"}, "planning"),
    ({"plan": ["read it", "fix it"], "cursor": 1}, "2/2"),
])
def test_the_status_bar_always_names_a_step(values, expected):
    """FR-702. A status bar that can show no step is the requirement unmet."""
    app = workspace(values)

    async def script(pilot):
        node = app.screen.query_one("#status", Static)
        assert expected in getattr(node.content, "plain", str(node.content))

    drive(app, script)


def test_the_composer_keeps_focus_so_typing_always_reaches_the_agent():
    app = workspace()

    async def script(pilot):
        assert app.screen.focused is app.screen.query_one("#composer")
        await pilot.press("alt+enter")
        await pilot.pause()
        assert app.screen.focused is app.screen.query_one("#composer")

    drive(app, script)


def test_the_pane_keys_are_not_swallowed_by_the_composer():
    """They are declared priority for exactly this reason, and an Input that
    ate alt+w would leave the layout unusable."""
    app = workspace()

    async def script(pilot):
        await pilot.press("alt+enter")
        await pilot.pause()
        assert app.screen.query_one("#composer", Input).value == ""

    drive(app, script)


# ================================================================ themes live

def test_ctrl_t_recolours_without_a_restart():
    app = workspace()

    async def script(pilot):
        seen = [app.theme]
        for _ in range(3):
            await pilot.press("ctrl+t")
            await pilot.pause()
            seen.append(app.theme)
        assert seen == ["noesis-mono", "catppuccin-mocha", "tokyo-night",
                        "noesis-mono"]
        assert app.ansi_color is True

    drive(app, script)


def test_ctrl_g_cycles_transparency_on_the_live_screen():
    app = workspace()

    async def script(pilot):
        seen = [app.mode]
        for _ in range(3):
            await pilot.press("ctrl+g")
            await pilot.pause()
            seen.append(app.mode)
            assert app.screen.has_class(f"-{app.mode}")
        assert seen == ["gaps", "bare", "opaque", "gaps"]

    drive(app, script)


@pytest.mark.parametrize("mode,bare", [("opaque", False), ("gaps", True),
                                       ("bare", True)])
def test_each_mode_paints_what_it_promises(mode, bare):
    app = workspace()
    app.mode = mode

    async def script(pilot):
        seen = set()
        for strip in app.screen._compositor.render_strips():
            for segment in strip:
                colour = segment.style.bgcolor if segment.style else None
                seen.add("default" if colour is None else colour.name)
        assert ("default" in seen) is bare
        assert seen - {"default"}, "nothing was painted at all"

    drive(app, script)


def test_no_colour_degrades_to_sixteen_colour_ansi():
    """Section 10.1's other half: under ansi_color=True an UNSET colour becomes
    a terminal ANSI colour. Measured once already - an unstyled scrollbar came
    out solid black in all three modes."""
    for mode in theme.MODES:
        app = workspace()
        app.mode = mode

        async def script(pilot, app=app, mode=mode):
            bad = set()
            for strip in app.screen._compositor.render_strips():
                for segment in strip:
                    for colour in ((segment.style.bgcolor,
                                    segment.style.color)
                                   if segment.style else ()):
                        if colour and (colour.name.startswith("color(")
                                       or colour.name == "#000000"):
                            bad.add(colour.name)
            assert not bad, f"{mode}: {sorted(bad)}"

        drive(app, script)


# ================================================================== the shim

def test_the_old_module_path_still_works():
    """`cli.py:495` imports `agent.tui` and calls `run(app, goal=, thread=)`.
    Nothing below the interface moved, so neither did that."""
    import inspect

    from agent import tui

    assert tui.run is screens.run
    assert tui.NoesisApp is screens.NoesisApp
    assert set(inspect.signature(tui.run).parameters) == {"graph_app", "goal",
                                                          "thread"}


def test_the_package_does_not_import_textual_to_be_imported():
    """NFR-602: `agent.ui.tiling` and `agent.setup` must stay reachable on a
    machine with no interface installed, so the app export is lazy."""
    import pathlib
    import subprocess
    import sys

    root = pathlib.Path(__file__).resolve().parent.parent
    code = (
        "import sys\n"
        "class Block:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'textual' or name.startswith('textual.'):\n"
        "            raise ImportError('textual is not installed')\n"
        "sys.meta_path.insert(0, Block())\n"
        "import agent.ui\n"
        "from agent.ui import tiling\n"
        "assert 'textual' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=root,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_every_pane_the_spec_names_exists():
    assert set(panes.PANES) == {"chat", "plan", "trace", "threads", "tasks",
                                "schedules", "doctor", "artifact"}


def test_a_long_path_loses_its_middle_and_keeps_its_filename():
    """The filename is the informative half; lopping the tail removes it."""
    out = panes.middle_out("agent/ui/very/deep/nested/context.py", 20)
    assert out.endswith("context.py") and out.startswith("agent")
    assert len(out) == 20


def test_the_tasks_badge_says_which_state_the_queue_is_in():
    """Section 5.4. Written and never rendered would be dead state, and a
    queue with something awaiting approval must be visible from the landing."""
    app = screens.NoesisApp(FakeGraph())

    async def script(pilot):
        # The count worker is still in flight and would overwrite this.
        await app.screen.workers.wait_for_complete()
        await pilot.pause()
        app.screen.counted({"/tasks": "2"}, "error")
        await pilot.pause()
        assert "●" in rendered(app)
        app.screen.counted({"/tasks": "2"}, "")
        await pilot.pause()
        assert "●" not in rendered(app)

    drive(app, script)


def test_ctrl_p_opens_the_command_palette():
    """Textual's own, bound by default - a second implementation would be a
    second list of commands to keep in step."""
    app = workspace()

    async def script(pilot):
        await pilot.press("ctrl+p")
        await pilot.pause()
        assert type(app.screen).__name__ == "CommandPalette"

    drive(app, script)


@pytest.mark.parametrize("mode", theme.MODES)
def test_the_chrome_stays_painted_in_every_mode(mode):
    """Section 10.3: the composer and the status bar must never be hard to read
    over someone's wallpaper. FR-702's step lives in that bar, so the bar losing
    its background takes the requirement with it.

    Checked on the WORKSPACE and not a stand-in: the phase-3 theme test uses its
    own host, so making the real chrome transparent passed it.
    """
    app = workspace()
    app.mode = mode

    async def script(pilot):
        strips = app.screen._compositor.render_strips()
        for name in ("#composer", "#status"):
            row = app.screen.query_one(name).region.y
            # EVERY cell, not any: the Input's cursor is painted on its own, so
            # "something on this row has a background" passes on a bare bar.
            bare = sum(len(segment.text) for segment in strips[row]
                       if not segment.style or segment.style.bgcolor is None
                       or segment.style.bgcolor.name == "default")
            assert bare == 0, f"{mode}: {name} has {bare} unpainted cells"

    drive(app, script)
