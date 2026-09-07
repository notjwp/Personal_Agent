"""The NOESIS terminal interface.

Deliberately empty of imports. `tiling.py` is pure and must stay importable on
a machine with no `textual` (NFR-602); a package that pulled the app in here
would break that silently, so the app export arrives with the app.
"""
