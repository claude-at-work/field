"""Decide which candidate to dispatch.

Three-step decision:
  1. If a scope rule exists for (name, cwd), use it.
  2. If the index has exactly one candidate (after content-hash dedup),
     use it.
  3. Otherwise prompt the user, remember the answer.

Returns a Resolution naming the snapshot, the in-snapshot abspath, and
whether the choice was pinned (informational; lineage records it).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import index, scope, lineage


@dataclass
class Resolution:
    name: str
    snapshot: str
    abspath: str           # in-snapshot, leading slash
    mode: str              # static | dynamic | nonelf
    source: str            # "scope" | "single" | "prompted" | "default"
    cwd_prefix: Optional[str] = None  # set when prompted+pinned


def _dedupe_by_content(rows: list[index.IndexRow]) -> list[index.IndexRow]:
    """Collapse rows that share (snapshot, sha256) — same bytes at /usr/bin
    and /bin shouldn't read as two candidates. Keep the first abspath we
    saw for each (snapshot, sha256) tuple."""
    seen: dict[tuple[str, str], index.IndexRow] = {}
    for r in rows:
        key = (r.snapshot, r.sha256 or r.abspath)
        if key not in seen:
            seen[key] = r
    return list(seen.values())


def _last_seen_for(snapshot: str, abspath: str) -> Optional[str]:
    """Best-effort: the most recent lineage row that dispatched this
    (snapshot, abspath). Cheap O(n) scan; lineage is a single TSV."""
    if not lineage.config.LINEAGE_FILE.exists():
        return None
    last_ts = None
    last_cwd = None
    with open(lineage.config.LINEAGE_FILE) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            ts, cwd, name, snap, ab = parts[:5]
            if snap == snapshot and ab == abspath:
                last_ts, last_cwd = ts, cwd
    if last_ts:
        return f"last used {last_ts} in {last_cwd}"
    return None


def _prompt(name: str, candidates: list[index.IndexRow], cwd: Path) -> tuple[index.IndexRow, bool]:
    """Interactive prompt. Returns (chosen, should_pin). Pinning defaults
    to True; user can decline at the second question."""
    print(f"field: {name!r} has {len(candidates)} candidates:", file=sys.stderr)
    for i, r in enumerate(candidates, 1):
        ctx = _last_seen_for(r.snapshot, r.abspath)
        ctx_str = f" ({ctx})" if ctx else " (never used through field)"
        print(f"  [{i}] {r.snapshot}: {r.abspath}{ctx_str}",
              file=sys.stderr)
    while True:
        sys.stderr.write(f"pick (1-{len(candidates)}): ")
        sys.stderr.flush()
        try:
            choice = sys.stdin.readline().strip()
        except (KeyboardInterrupt, EOFError):
            print("\nfield: aborted", file=sys.stderr)
            raise SystemExit(130)
        try:
            idx = int(choice)
            if 1 <= idx <= len(candidates):
                break
        except ValueError:
            pass
        print(f"field: bad choice {choice!r}", file=sys.stderr)
    chosen = candidates[idx - 1]

    sys.stderr.write(f"remember this for {cwd} and subdirs? [Y/n]: ")
    sys.stderr.flush()
    try:
        ans = sys.stdin.readline().strip().lower()
    except (KeyboardInterrupt, EOFError):
        ans = ""
    should_pin = ans in ("", "y", "yes")
    return chosen, should_pin


def resolve(name: str, cwd: Path,
            mode_filter: Optional[str] = None,
            interactive: bool = True) -> Optional[Resolution]:
    """Pick a candidate for `name` from the index. Returns None if the
    index has no candidate at all."""
    cwd = cwd.resolve()

    rule = scope.lookup(name, cwd)
    if rule:
        return Resolution(name=name, snapshot=rule.snapshot, abspath=rule.abspath,
                          mode="(from scope)", source="scope")

    raw_cands = index.candidates_for(name, mode_filter=mode_filter)
    cands = _dedupe_by_content(raw_cands)

    if not cands:
        return None

    if len(cands) == 1:
        c = cands[0]
        return Resolution(name=name, snapshot=c.snapshot, abspath=c.abspath,
                          mode=c.mode, source="single")

    if not interactive or not sys.stdin.isatty():
        c = cands[0]
        return Resolution(name=name, snapshot=c.snapshot, abspath=c.abspath,
                          mode=c.mode, source="default")

    chosen, should_pin = _prompt(name, cands, cwd)
    cwd_prefix: Optional[str] = None
    if should_pin:
        scope.pin(name, chosen.snapshot, chosen.abspath, cwd)
        cwd_prefix = str(cwd)
    return Resolution(name=name, snapshot=chosen.snapshot, abspath=chosen.abspath,
                      mode=chosen.mode, source="prompted", cwd_prefix=cwd_prefix)
