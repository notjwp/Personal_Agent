"""CSV import/export for raw score sheets."""
import csv
from pathlib import Path


def load_scores(path: Path) -> dict[str, list[float]]:
    """Read `name,score,score,...` rows into a mapping."""
    records: dict[str, list[float]] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or row[0].startswith("#"):
                continue
            records[row[0]] = [float(cell) for cell in row[1:] if cell.strip()]
    return records


def save_scores(path: Path, records: dict[str, list[float]]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for name in sorted(records):
            writer.writerow([name, *records[name]])
