"""Shared fixtures.

`tmp_workspace` redirects the workspace root at a temp directory. It patches the
attributes on `agent.config` itself, which only works because every module reads
`config.WORKSPACE` late rather than binding the name at import time.
"""
import pytest

from agent import config


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """Point the workspace and artifacts roots at a temp directory."""
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(config, "ARTIFACTS", tmp_path / ".agent" / "artifacts")
    return tmp_path


@pytest.fixture
def fresh_app(tmp_workspace, monkeypatch):
    """A compiled graph with its own checkpoint database, rebuilt for this test.

    `get_app()` memoises, so the cached instance is cleared first; monkeypatch
    restores it afterwards.
    """
    from agent import graph

    monkeypatch.setattr(config, "STATE_DB", tmp_workspace / ".agent" / "state.db")
    monkeypatch.setattr(graph, "_APP", None)
    return graph.get_app()
