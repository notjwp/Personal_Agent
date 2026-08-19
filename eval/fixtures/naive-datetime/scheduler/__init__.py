"""Calendar events and time-window queries."""

__version__ = "0.1.0"

# Deliberately no re-exports. If this module imported every submodule, one broken
# import would fail collection of ALL test files and the rig could not tell a
# broken fixture apart from a broken environment.
