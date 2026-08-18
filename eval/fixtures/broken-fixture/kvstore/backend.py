"""JSON-file persistence for the store."""
import json
from pathlib import Path


class FileBackend:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        text = self.path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
