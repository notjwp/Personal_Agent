"""Score aggregation and letter grades. Pure standard library."""

BANDS = ((90, "A"), (80, "B"), (70, "C"), (60, "D"), (0, "F"))


def average(scores: list[float]) -> float:
    if not scores:
        raise ValueError("cannot average an empty score list")
    return sum(scores) / len(scores)


def letter(score: float) -> str:
    for threshold, grade in BANDS:
        if score >= threshold:
            return grade
    return "F"


def summarise(records: dict[str, list[float]]) -> list[dict]:
    """One row per student: name, mean score, letter grade."""
    rows = []
    for name in sorted(records):
        mean = average(records[name])
        rows.append({"name": name, "average": round(mean, 2), "grade": letter(mean)})
    return rows
