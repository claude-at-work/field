"""field CLI.

Stage 0 surface:
    field index <snapshot-root> [--name NAME]
    field run <name> [argv...]
    field log [--tail N]
    field list [--mode MODE]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, index, lineage


def cmd_index(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    name = args.name or root.name
    print(f"scanning {root} as snapshot={name!r}")
    new_rows = list(index.scan_snapshot(root, name))
    existing = [r for r in index.read_index() if r.snapshot != name]
    rows = existing + new_rows
    index.write_index(rows)
    counts = {"static": 0, "dynamic": 0, "nonelf": 0}
    for r in new_rows:
        counts[r.mode] = counts.get(r.mode, 0) + 1
    print(f"  scanned: {len(new_rows)} entries")
    print(f"  static:  {counts.get('static', 0)}")
    print(f"  dynamic: {counts.get('dynamic', 0)}")
    print(f"  nonelf:  {counts.get('nonelf', 0)}")
    print(f"  total:   {len(rows)} entries across {len({r.snapshot for r in rows})} snapshot(s)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    name = args.name
    cands = index.candidates_for(name, mode_filter="static")
    if not cands:
        all_cands = index.candidates_for(name, mode_filter=None)
        if all_cands:
            print(f"field: {name!r} found in index but only as "
                  f"{', '.join(sorted({c.mode for c in all_cands}))} — "
                  f"stage 0 only dispatches static binaries", file=sys.stderr)
        else:
            print(f"field: {name!r} not in index", file=sys.stderr)
        return 127
    if len(cands) > 1:
        print(f"field: {name!r} has {len(cands)} candidates "
              f"({', '.join(c.snapshot for c in cands)}) — "
              f"multi-candidate UX is stage 3; using first", file=sys.stderr)
    chosen = cands[0]
    snapshot_root = config.SNAPSHOTS_DIR / chosen.snapshot
    target = Path(str(snapshot_root) + chosen.abspath)
    if not target.exists():
        print(f"field: indexed at {chosen.abspath} but missing on disk: {target}",
              file=sys.stderr)
        return 127
    argv = [name, *args.argv]
    pid = os.fork()
    if pid == 0:
        try:
            os.execv(str(target), argv)
        except OSError as exc:
            print(f"field: exec failed: {exc}", file=sys.stderr)
            os._exit(127)
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        ec = os.WEXITSTATUS(status)
    elif os.WIFSIGNALED(status):
        ec = 128 + os.WTERMSIG(status)
    else:
        ec = -1
    lineage.record(name=name, snapshot=chosen.snapshot, abspath=chosen.abspath,
                   mode=chosen.mode, argv=argv, cwd=Path.cwd(), exit_code=ec)
    return ec


def cmd_log(args: argparse.Namespace) -> int:
    lines = lineage.tail(args.tail)
    if not lines:
        print("no lineage yet")
        return 0
    print(f"{'TIMESTAMP':<19} {'EXIT':<5} {'NAME':<20} {'SNAPSHOT':<24} ARGV")
    print("─" * 110)
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        ts, cwd, name, snap, abspath, mode, ec, argv = parts[:8]
        print(f"{ts:<19} {ec:<5} {name:<20} {snap:<24} {argv}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = index.read_index()
    if args.mode:
        rows = [r for r in rows if r.mode == args.mode]
    if not rows:
        print("index is empty (run `field index <snapshot-root>` first)")
        return 0
    print(f"{'NAME':<28} {'MODE':<8} {'SNAPSHOT':<24} {'SIZE':>10}  PATH")
    print("─" * 110)
    for r in sorted(rows, key=lambda r: (r.name, r.snapshot)):
        print(f"{r.name:<28} {r.mode:<8} {r.snapshot:<24} {r.size:>10}  {r.abspath}")
    print(f"\n{len(rows)} entries")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="field")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="scan a snapshot root into the index")
    p_index.add_argument("root", help="path to snapshot root (e.g. /root/kali-fs)")
    p_index.add_argument("--name", help="snapshot name (default: basename of root)")
    p_index.set_defaults(func=cmd_index)

    p_run = sub.add_parser("run", help="dispatch a binary by name")
    p_run.add_argument("name")
    p_run.add_argument("argv", nargs=argparse.REMAINDER)
    p_run.set_defaults(func=cmd_run)

    p_log = sub.add_parser("log", help="show recent dispatches")
    p_log.add_argument("--tail", type=int, default=20)
    p_log.set_defaults(func=cmd_log)

    p_list = sub.add_parser("list", help="list indexed binaries")
    p_list.add_argument("--mode", choices=["static", "dynamic", "nonelf"])
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
