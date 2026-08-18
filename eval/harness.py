"""Practice-project runner and scorer.

Two modes, one file (the build spec permits a single harness file):

  OUTER (host)       python eval/harness.py [--split dev] [--case ID] [--runs N]
                     selects cases, spawns ONE container per case-run, aggregates,
                     prints `pass N/M`.

  INNER (container)  python eval/harness.py --run-case ID --run-index K --out DIR
                     runs setup, invokes the agent, runs check, writes one trace.

Why a container per case-run: `missing-dep` installs a package into site-packages.
Sharing one container across runs would leave it installed, so repeats of that case
would pass without the agent doing anything - quietly faking the baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IMAGE = "personal-agent"
ENV_FILE = REPO / ".env"

# How the inner runner tells the outer driver what happened.
#
# The distinction is the whole point: a BLOCKED run never reached the model, so it
# measured nothing and must not be scored - counting it as a failure understates
# the agent and corrupts every comparison made against the baseline afterwards. A
# MISCONFIGURED one cannot be fixed by retrying, so the suite stops at once rather
# than recording fifteen identical "failures".
COMPLETED, BLOCKED, MISCONFIGURED = 0, 3, 4
BLOCKED_RETRIES = 2

# Everything the container needs to reach a model. Forwarded by name, never by
# value, so a key is never written into a command line, a log or a trace.
FORWARDED_ENV = (
    "AGENT_PROVIDER",
    "ANTHROPIC_API_KEY",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
    "NIM_BASE_URL",
    "NIM_MODEL",
)

# The agent has to reach a model, so a live run cannot use --network none. The spec
# asks for egress restricted to a domain ALLOWLIST, not for no egress at all; an
# allowlist needs a proxy, which is Phase E hardening. Until then this is an
# explicit, recorded gap rather than a silent one.
#
# Hermeticity of the `missing-dep` case does NOT depend on this: /etc/pip.conf sets
# no-index, so pip resolves from /wheels whether or not the network is up.
NETWORK = os.environ.get("AGENT_NETWORK", "bridge")

# Running `python eval/harness.py` puts eval/ on sys.path, not the project root,
# so `import agent` would fail. Fixing it here rather than via PYTHONPATH keeps the
# harness working under every invocation, on host and container alike.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def load_cases() -> list[dict]:
    text = (REPO / "eval" / "tasks.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def count_tool_calls(messages: list[dict]) -> int:
    return sum(
        1
        for m in messages
        if m.get("role") == "assistant" and isinstance(m.get("content"), list)
        for b in m["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use"
    )


PROTECTED = "test_*.py"


def restore_protected_tests(case: dict) -> list[str]:
    """Put the practice project's assertions back before scoring.

    The tests ARE the specification, so passing by editing them is not passing.
    Observed live: the agent overwrote tests/test_parser.py three times and never
    touched the module that actually had the bug.

    `conftest.py` is deliberately NOT protected - one practice project's bug
    genuinely lives there, and restoring it would make that case impossible.

    Test files the agent invented are removed too, and that is a fairness measure
    as much as an integrity one: a broken scratch test left in tests/ would fail
    the suite for a reason that has nothing to do with the case.

    Returns what changed, so a rewritten test surfaces in the trace as a wasted
    turn instead of vanishing.
    """
    from agent import config as settings

    src = REPO / "eval" / "fixtures" / case["id"] / "tests"
    dst = settings.WORKSPACE / "tests"
    touched: list[str] = []
    if not src.is_dir():
        return touched

    for original in sorted(src.glob(PROTECTED)):
        target = dst / original.name
        want = original.read_bytes()
        if not target.exists() or target.read_bytes() != want:
            touched.append(f"restored {original.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(want)

    allowed = {q.name for q in src.glob(PROTECTED)}
    if dst.is_dir():
        for extra in sorted(dst.glob(PROTECTED)):
            if extra.name not in allowed:
                touched.append(f"removed invented {extra.name}")
                extra.unlink()

    return touched


# --------------------------------------------------------------------- outer

def read_rows(out: Path) -> list[dict]:
    summary = out / "summary.jsonl"
    if not summary.exists():
        return []
    return [json.loads(l) for l in summary.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def latest_rows(rows: list[dict]) -> list[dict]:
    """One row per (id, run_index) - the last one written wins.

    summary.jsonl is append-only, so a retried case-run leaves its earlier blocked
    row in place. Counting both would double-count a single case-run and quietly
    move the headline number.
    """
    by_key: dict[tuple, dict] = {}
    for row in rows:
        by_key[(row["id"], row["run_index"])] = row
    return list(by_key.values())


def completed(rows: list[dict]) -> set[tuple]:
    """Case-runs that actually finished. A blocked row is NOT complete - retrying
    it is precisely what --continue is for."""
    return {(r["id"], r["run_index"]) for r in latest_rows(rows)
            if r.get("status", "ok") == "ok"}


def summarise(rows: list[dict]) -> str:
    """Render the score. Pure - takes rows, returns text, touches nothing.

    Pure so it can be tested without running anything, which matters more here
    than usual: this function decides the headline number, and getting it wrong
    does not crash, it just reports something other than the truth.
    """
    rows = latest_rows(rows)
    scored = [r for r in rows if r.get("status", "ok") == "ok"]
    blocked = [r for r in rows if r.get("status") == "blocked"]

    head = f"pass {sum(1 for r in scored if r['pass'])}/{len(scored)}"
    if blocked:
        head += f"   ({len(blocked)} blocked, excluded - not counted as failures)"
    lines = [head]

    # Anything that makes the number untrustworthy goes ABOVE the table, not
    # buried in a trace nobody opens.
    warnings = []
    tampered = sum(1 for r in scored if r.get("tampered"))
    if tampered:
        warnings.append(f"{tampered} run(s) edited the tests they are judged by")
    if any(r["verdict"] == "setup-failed" for r in scored):
        warnings.append("a case failed SETUP - that is the rig, not the agent")
    for w in warnings:
        lines.append(f"  ! {w}")

    models = sorted({f"{r.get('provider','?')}/{r.get('model','?')}" for r in scored})
    if len(models) > 1:
        lines.append(f"  ! MIXED PROVIDERS in one run: {', '.join(models)}")

    ids = sorted({r["id"] for r in scored})
    if ids:
        lines += ["", f"{'case':<16}{'pass':<7}{'verdicts':<24}{'turns':<14}"
                      f"{'tokens(med)':>11}  tamper"]
    for cid in ids:
        group = sorted((r for r in scored if r["id"] == cid),
                       key=lambda r: r["run_index"])
        counts: dict[str, int] = {}
        for r in group:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        lines.append(
            f"{cid:<16}"
            f"{sum(1 for r in group if r['pass'])}/{len(group):<5}"
            f"{' '.join(f'{v} x{n}' for v, n in sorted(counts.items())):<24}"
            # The raw counts, not a median: §9 asks for variance, and 3/10/4 shows
            # it where "median 4" conceals it.
            f"{'/'.join(str(r['turns']) for r in group):<14}"
            f"{int(statistics.median([r['tokens'] for r in group])):>11,}"
            f"  {sum(r.get('tampered', 0) for r in group)}")

    dist: dict[str, int] = {}
    for r in scored:
        dist[r["verdict"]] = dist.get(r["verdict"], 0) + 1
    if dist:
        # Not decoration. This distribution is what earns the next layer:
        # `compact` dominating earns compaction, `stuck` at the cap earns the plan
        # node. It replaces the spec's prediction with a measurement.
        lines += ["", "verdicts: " + ", ".join(
            f"{v} {n}" for v, n in sorted(dist.items(), key=lambda kv: (-kv[1], kv[0])))]
    return "\n".join(lines)


def run_dir(args, cases) -> Path | None:
    """Choose the output directory, creating or resuming one.

    The manifest is what makes resuming safe: it records what the directory
    describes, so a second invocation cannot quietly pour a different population
    into the same score.
    """
    root = REPO / "eval" / "runs"
    want = {"split": args.split, "case": args.case,
            "cases": sorted(c["id"] for c in cases), "runs": args.runs}

    if not args.continue_:
        out = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text(
            json.dumps({**want, "image": IMAGE,
                        "started": datetime.now(timezone.utc).isoformat()}, indent=2),
            encoding="utf-8")
        return out

    existing = sorted(p for p in root.glob("*") if (p / "manifest.json").exists())
    if not existing:
        print("nothing to continue: no previous run has a manifest", file=sys.stderr)
        return None
    out = existing[-1]
    have = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    differs = {k: (have.get(k), v) for k, v in want.items() if have.get(k) != v}
    if differs:
        print(f"refusing to continue {out.name}: it describes a different run",
              file=sys.stderr)
        for key, (was, now) in differs.items():
            print(f"    {key}: recorded {was!r}, requested {now!r}", file=sys.stderr)
        return None
    # Say which directory, always. Appending into the wrong one would corrupt a
    # baseline with no visible symptom.
    print(f"continuing {out.name}")
    return out


def spawn(case: dict, run_index: int, out: Path) -> int:
    cmd = [
        "docker", "run", "--rm", "--network", NETWORK,
        "-v", f"{REPO.as_posix()}:/app",
        "-v", f"{(REPO / 'eval' / 'workspace').as_posix()}:/workspace",
    ]
    # .env first, then the real environment, so an exported variable
    # deliberately overrides the file rather than the other way round.
    if ENV_FILE.exists():
        cmd += ["--env-file", str(ENV_FILE)]
    for name in FORWARDED_ENV:
        if os.environ.get(name):
            cmd += ["-e", name]
    cmd += [
        IMAGE, "python", "eval/harness.py",
        "--run-case", case["id"],
        "--run-index", str(run_index),
        "--out", f"/app/eval/runs/{out.name}",
    ]
    return subprocess.run(cmd, check=False).returncode


def outer(args) -> int:
    cases = [
        c for c in load_cases()
        if (args.case is None or c["id"] == args.case)
        and (args.split is None or c["split"] == args.split)
    ]
    if not cases:
        print("no cases matched", file=sys.stderr)
        return 2

    out = run_dir(args, cases)
    if out is None:
        return 2

    planned = [(c, i) for c in cases for i in range(args.runs)]
    already = completed(read_rows(out))
    todo = [(c, i) for c, i in planned if (c["id"], i) not in already]
    if already:
        print(f"{len(already)} of {len(planned)} case-runs already complete; "
              f"{len(todo)} to go")

    for position, (case, run_index) in enumerate(todo):
        print(f"-> {case['id']} run {run_index}", flush=True)
        for attempt in range(BLOCKED_RETRIES + 1):
            code = spawn(case, run_index, out)
            if code == MISCONFIGURED:
                print("\naborting: the provider is misconfigured. Fix it, then "
                      "re-run with --continue to keep the work done so far.",
                      file=sys.stderr)
                return 2
            if code != BLOCKED:
                break
            if attempt < BLOCKED_RETRIES:
                wait = max(args.pace, 1) * 4 * (attempt + 1)
                # Announced, never silent: an unexplained pause is indistinguishable
                # from a hang, which this project has already paid for twice.
                print(f"   blocked; waiting {wait}s before retry "
                      f"{attempt + 1}/{BLOCKED_RETRIES}", flush=True)
                time.sleep(wait)
        if args.pace and position < len(todo) - 1:
            time.sleep(args.pace)

    rows = read_rows(out)
    print()
    print(summarise(rows))
    print(f"\ntraces: {out}")

    missing = len(planned) - len(completed(rows))
    if missing:
        # A rig failure, not a score. `pass 0/5` is a legitimate result and exits 0;
        # case-runs that never produced a result must not look the same.
        print(f"INCOMPLETE: {missing} of {len(planned)} case-run(s) have no result. "
              f"Re-run with --continue.", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------- inner

def inner(args) -> int:
    case = next(c for c in load_cases() if c["id"] == args.run_case)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # shell=True is deliberate: `setup` and `check` are shell command strings by
    # specification (the check contains `&&`), and their only source is the
    # committed tasks.jsonl. Never build these strings from model output.
    setup = subprocess.run(case["setup"], shell=True, cwd=REPO,
                           capture_output=True, text=True)
    if setup.returncode != 0:
        # A broken rig, not a failed agent. Recorded distinctly so it can never be
        # mistaken for the agent missing the case.
        return record(out, case, args.run_index, passed=False, verdict="setup-failed",
                      seconds=0.0, state=None, note=setup.stderr[-2000:])

    # Imported after setup so a rig failure needs no agent.
    from agent.graph import get_app, new_state
    from agent.provider import ProviderMisconfigured, ProviderUnavailable

    state = new_state(case["goal"], case["max_turns"], case["budget"])
    trace: list[dict] = []          # nodes append here; see agent/graph.py _log()
    started = time.monotonic()
    try:
        final = get_app().invoke(state, {"configurable": {
            "thread_id": f"{case['id']}-{args.run_index}",
            "autonomous": True,     # every `confirm` becomes `deny`; nothing blocks
            "trace": trace,
        }})
        note = ""
    except ProviderMisconfigured as exc:
        # No row at all. Nothing was measured, and writing a row would imply
        # otherwise; the outer driver stops the whole suite on this code.
        print(f"FATAL: {exc}", file=sys.stderr)
        return MISCONFIGURED
    except ProviderUnavailable as exc:
        # A row, but no score. The outer driver retries this same case-run, and
        # aggregation keeps it out of the denominator.
        record(out, case, args.run_index, passed=False, verdict="blocked",
               seconds=time.monotonic() - started, state=None, note=str(exc),
               status="blocked")
        return BLOCKED
    except Exception as exc:
        # A crashed agent IS a result - our bug, a malformed tool call, a bad
        # request - and must be scored. Only the two cases above are excused.
        final, note = state, f"{type(exc).__name__}: {exc}"
    seconds = time.monotonic() - started

    tampered = restore_protected_tests(case)
    if tampered:
        trace.append({"kind": "tamper", "files": tampered})

    check = subprocess.run(case["check"], shell=True, capture_output=True, text=True)
    return record(out, case, args.run_index, passed=check.returncode == 0,
                  verdict=final.get("verdict") or "none", seconds=seconds,
                  state=final, note=note or check.stdout[-2000:], trace=trace)


def record(out: Path, case: dict, run_index: int, *, passed: bool, verdict: str,
           seconds: float, state: dict | None, note: str, trace=(),
           status: str = "ok") -> int:
    state = state or {}
    calls = [t for t in trace if t.get("kind") == "tool"]
    models = [t for t in trace if t.get("kind") == "model"]

    # Recorded per row, not once per suite: the row is what survives, and a score
    # whose model is unknown cannot be compared to anything.
    from agent import config as settings
    model = settings.MODEL if settings.PROVIDER == "anthropic" else settings.OPENAI_MODEL

    row = {
        "id": case["id"],
        "run_index": run_index,
        # "ok" means measured - pass or fail. "blocked" means it never reached the
        # model, carries no score, and is excluded from the denominator.
        "status": status,
        "provider": settings.PROVIDER,
        "model": model,
        "pass": passed,
        "verdict": verdict,
        "turns": state.get("turns", 0),
        "tokens": state.get("spent_tokens", 0),
        # Kept apart from billed tokens: a cache read costs about a tenth of a fresh
        # one, so folding them together would misstate both.
        "cache_read_tokens": sum(m.get("cache_read_tokens", 0) for m in models),
        "model_calls": len(models),
        "tool_calls": len(calls) if trace else count_tool_calls(state.get("messages", [])),
        "spills": sum(1 for c in calls if c.get("spill_path")),
        "tool_errors": sum(1 for c in calls if c.get("is_error")),
        # Non-zero means the agent edited the assertions it is judged by.
        "tampered": sum(len(t.get("files", [])) for t in trace
                        if t.get("kind") == "tamper"),
        "seconds": round(seconds, 2),
    }
    with (out / "summary.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")

    (out / f"{case['id']}-{run_index}.json").write_text(
        json.dumps({**row, "goal": case["goal"], "note": note,
                    "messages": state.get("messages", []),
                    "calls": calls, "trace": list(trace)}, indent=2),
        encoding="utf-8")

    label = "BLOCKED" if status == "blocked" else ("PASS" if passed else "FAIL")
    print(f"{case['id']} run {run_index}: {label} ({verdict})")
    return COMPLETED


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--split")
    p.add_argument("--case")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--pace", type=int, default=15,
                   help="seconds between case-runs; the free tier refuses bursts")
    # `continue` is a keyword, hence the dest.
    p.add_argument("--continue", dest="continue_", action="store_true",
                   help="resume the newest run directory instead of starting one")
    p.add_argument("--run-case")
    p.add_argument("--run-index", type=int, default=0)
    p.add_argument("--out")
    args = p.parse_args()
    return inner(args) if args.run_case else outer(args)


if __name__ == "__main__":
    raise SystemExit(main())
