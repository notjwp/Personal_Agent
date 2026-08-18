"""Human-readable rendering of a grade summary."""
from tabulate import tabulate

from gradebook.grades import summarise

HEADERS = ("Student", "Average", "Grade")


def render_table(records: dict[str, list[float]]) -> str:
    rows = [(r["name"], r["average"], r["grade"]) for r in summarise(records)]
    return tabulate(rows, headers=HEADERS, tablefmt="github")
