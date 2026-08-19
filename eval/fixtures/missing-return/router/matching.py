"""Matching a path to a route."""


def find(routes, pattern: str):
    """The route registered for an exact pattern, or None."""
    for route in routes:
        if route.pattern == pattern:
            return route
    return None


def deepest(routes):
    return max(routes, key=lambda r: r.depth, default=None)
