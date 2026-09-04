"""recall@k for keyword retrieval over the 170-episode corpus.

FR-408's gate: "reopens if a real corpus reproduces the shortfall after the query
fix, which is the condition to re-measure - not to assume." This is that
re-measurement. The previous one used 36 goals and 6 pairs.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CORPUS = Path(__file__).resolve().parent / "fixtures" / "recall-corpus.jsonl"


def main() -> int:
    home = Path(tempfile.mkdtemp())
    import os
    os.environ["AGENT_HOME"] = str(home)

    from agent import config, memory
    config.AGENT_HOME = home
    config.MEMORY_DB = home / "memory.db"

    rows = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = {}
    for i, r in enumerate(rows):
        rowid = memory.write_episode(f"t{i}", r["goal"], "done", r["answer"], [], [])
        ids[r["goal"]] = rowid

    pairs = [r for r in rows if r.get("query")]
    hits = {1: 0, 3: 0, 5: 0}
    misses = []
    for r in pairs:
        found = memory.search(r["query"], limit=5)
        order = [f["goal"] for f in found]
        target = r["goal"]
        rank = order.index(target) + 1 if target in order else None
        for k in (1, 3, 5):
            if rank and rank <= k:
                hits[k] += 1
        if not rank or rank > 3:
            misses.append((r["query"], target, order[:3]))

    n = len(pairs)
    print(f"corpus: {len(rows)} episodes, {n} ground-truth pairs")
    print()
    for k in (1, 3, 5):
        print(f"  recall@{k}: {hits[k]}/{n}  ({100*hits[k]/n:.0f}%)")
    print()
    if misses:
        print(f"MISSES ({len(misses)}):")
        for q, t, got in misses:
            print(f"  query : {q}")
            print(f"  wanted: {t}")
            print(f"  got   : {got[0][:70] if got else '(nothing)'}")
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
