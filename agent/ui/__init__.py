"""The NOESIS terminal interface.

`NoesisApp` is exported LAZILY on purpose. `tiling.py` is pure and `setup.py`'s
entry points are stdlib, and both must stay importable on a machine with no
`textual` (NFR-602); a package that pulled the app in at module scope would
break that silently, and a test asserts it does not.
"""

__all__ = ["NoesisApp"]


def __getattr__(name: str):
    if name == "NoesisApp":
        from agent.ui.screens import NoesisApp

        return NoesisApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
