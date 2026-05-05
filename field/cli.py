"""field CLI.

Stage 1 surface:
    field index <snapshot-root> [--name NAME]
    field run <name> [argv...]                       # via resolve+substrate
    field probe                                      # write host portrait
    field log [--tail N]
    field list [--mode MODE]
    field scope show
    field scope pin <name> <snapshot> [--cwd PATH]
    field scope unpin <name> [--cwd PATH]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config, index, lineage, resolve as resolve_mod, scope as scope_mod, substrate as substrate_mod


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
    # Make sure the host portrait is written before first dispatch — the
    # substrate ladder needs to know what this kernel can host.
    if not config.HOST_FILE.exists():
        menu = substrate_mod.probe()
        substrate_mod.write_host_portrait(menu)
        print(f"  substrates: bwrap={menu.bwrap} proot={menu.proot} "
              f"ld_library_path=True direct=True")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    menu = substrate_mod.probe()
    substrate_mod.write_host_portrait(menu)
    print(f"host portrait at {config.HOST_FILE}")
    print(f"  bwrap            = {menu.bwrap}"
          + (f"  ({menu.bwrap_reason})" if not menu.bwrap and menu.bwrap_reason else ""))
    print(f"  proot            = {menu.proot}")
    print(f"  ld_library_path  = {menu.ld_library_path}")
    print(f"  direct           = {menu.direct}")
    print(f"  best available   = {substrate_mod.best_substrate(menu)}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    name = args.name
    cwd = Path.cwd()

    res = resolve_mod.resolve(name, cwd, mode_filter=None, interactive=True)
    if res is None:
        print(f"field: {name!r} not in index", file=sys.stderr)
        return 127

    snapshot_root = config.SNAPSHOTS_DIR / res.snapshot
    target = Path(str(snapshot_root) + res.abspath)
    if not target.exists():
        print(f"field: indexed at {res.abspath} but missing on disk: {target}",
              file=sys.stderr)
        return 127

    # Pick the substrate. Static binaries can short-circuit through 'direct'
    # even on hosts that have bwrap; everything else routes to the best
    # available isolation tier.
    menu = substrate_mod.read_host_portrait() or substrate_mod.probe()
    if res.mode == "static":
        chosen_substrate = "direct"
    else:
        chosen_substrate = substrate_mod.best_substrate(menu)

    argv = [name, *args.argv]
    try:
        ec = substrate_mod.dispatch(chosen_substrate, snapshot_root, target, argv)
    except (FileNotFoundError, ValueError) as exc:
        print(f"field: dispatch failed via {chosen_substrate}: {exc}",
              file=sys.stderr)
        ec = 127

    lineage.record(name=name, snapshot=res.snapshot, abspath=res.abspath,
                   mode=res.mode, argv=argv, cwd=cwd, exit_code=ec,
                   substrate=chosen_substrate)
    return ec


def cmd_log(args: argparse.Namespace) -> int:
    lines = lineage.tail(args.tail)
    if not lines:
        print("no lineage yet")
        return 0
    print(f"{'TIMESTAMP':<19} {'EXIT':<5} {'SUBSTRATE':<16} {'NAME':<18} {'SNAPSHOT':<20} ARGV")
    print("─" * 120)
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        # Backward compat: old lines without substrate column have 8 fields,
        # new lines have 9.
        if len(parts) == 8:
            ts, cwd, name, snap, abspath, mode, ec, argv = parts
            sub = "(legacy)"
        elif len(parts) >= 9:
            ts, cwd, name, snap, abspath, mode, sub, ec, argv = parts[:9]
        else:
            continue
        print(f"{ts:<19} {ec:<5} {sub:<16} {name:<18} {snap:<20} {argv}")
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


def cmd_scope_show(args: argparse.Namespace) -> int:
    rules = scope_mod.all_rules()
    if not rules:
        print("no scope rules")
        return 0
    print(f"{'NAME':<18} {'SNAPSHOT':<24} {'CWD-PREFIX':<40} {'PATH':<25} PINNED-AT")
    print("─" * 130)
    for r in rules:
        print(f"{r.name:<18} {r.snapshot:<24} {r.cwd_prefix:<40} {r.abspath:<25} {r.pinned_at}")
    return 0


def cmd_scope_pin(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    cands = index.candidates_for(args.name, mode_filter=None)
    cands = [c for c in cands if c.snapshot == args.snapshot]
    if not cands:
        print(f"field: no {args.name!r} in snapshot {args.snapshot!r}",
              file=sys.stderr)
        return 1
    abspath = cands[0].abspath
    rule = scope_mod.pin(args.name, args.snapshot, abspath, cwd)
    print(f"pinned {rule.name} → {rule.snapshot}:{rule.abspath} for {rule.cwd_prefix}")
    return 0


def cmd_scope_unpin(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).resolve() if args.cwd else None
    n = scope_mod.unpin(args.name, cwd)
    print(f"removed {n} rule(s) for {args.name!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="field")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="scan a snapshot root into the index")
    p_index.add_argument("root", help="path to snapshot root (e.g. /root/kali-fs)")
    p_index.add_argument("--name", help="snapshot name (default: basename of root)")
    p_index.set_defaults(func=cmd_index)

    p_probe = sub.add_parser("probe", help="detect substrate availability, write host portrait")
    p_probe.set_defaults(func=cmd_probe)

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

    p_scope = sub.add_parser("scope", help="manage scope-pin rules")
    p_scope_sub = p_scope.add_subparsers(dest="scope_cmd", required=True)

    p_scope_show = p_scope_sub.add_parser("show", help="list all scope rules")
    p_scope_show.set_defaults(func=cmd_scope_show)

    p_scope_pin = p_scope_sub.add_parser("pin", help="pin a binary to a snapshot for a cwd")
    p_scope_pin.add_argument("name")
    p_scope_pin.add_argument("snapshot")
    p_scope_pin.add_argument("--cwd", help="cwd prefix (default: $PWD)")
    p_scope_pin.set_defaults(func=cmd_scope_pin)

    p_scope_unpin = p_scope_sub.add_parser("unpin", help="remove pin(s) for a binary")
    p_scope_unpin.add_argument("name")
    p_scope_unpin.add_argument("--cwd", help="specific cwd prefix; default: all pins for name")
    p_scope_unpin.set_defaults(func=cmd_scope_unpin)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
