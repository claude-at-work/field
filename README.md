# field

**Retrofittable OS-layer dispatcher.** Sits on a messy real filesystem, watches which binaries the user reaches for, records lineage, and (in later stages) distills the lineage into a deployment manifest. The companion to [bubble](https://github.com/claude-at-work/Bubblev2) — where bubble does *projection* (clean reproducible deployment from observed Python imports), field does *capture*.

Together they cover the arc from "this machine accumulated five years of cruft" to "here's the clean version with only what you actually use."

## the shape

```
~/.field/
  snapshots/<name>/        # bind-mounted or symlinked prior root
  index.tsv                # name → snapshot:abspath, mode, sha256
  lineage.tsv              # ts, cwd, name, snapshot, abspath, exit, argv
```

Bash and zsh's `command_not_found_handle` re-execs through `field run`. The dispatcher looks up the name in the index, picks a candidate, runs the binary in the right substrate, records the dispatch. A binary your fresh root doesn't have but a snapshot does just *works* — and the lineage log accumulates a record of what you actually use.

## what's shipped today

**Stage 0 — static-binary probe.** Index/run/log/list commands; bash+zsh `command_not_found_handle` hook installer; static-ELF dispatch via `os.fork()` + `os.execv()`. End-to-end on the easy half of the problem.

The empirical finding from running it: on a desktop Linux fs, only 4/1815 binaries are statically-linked (just `ld.so` and `ldconfig`). Stage 0 proves the architecture. Utility starts at Stage 1.

## what's coming

See [`docs/plan.md`](docs/plan.md) for the staged build order and [`docs/stage1.md`](docs/stage1.md) for the user story driving Stage 1 (dynamic-binary substrate via `bwrap`, with multi-candidate UX folded in).

[`docs/notebook.md`](docs/notebook.md) is the running register of anomalies, hidden potential, soft spots, and conventions that should swap with novelty (or vice versa) — entries written during the work, not after. Read that for the inside-the-loop view of how the architecture meets real binaries.

## installation

Stage 0 is a single Python module + a small shell hook.

```sh
git clone https://github.com/claude-at-work/field.git
cd field
install -m 755 scripts/field-shim.sh ~/.local/bin/field    # OR see scripts/install.sh once it lands
./scripts/install_bash_hook.sh                              # idempotent; bash and zsh
field index /path/to/snapshot --name <snapshot-name>
```

`field --help` lists the subcommands.

## what field is not

- **Not a sandbox.** Stage 1+ uses mount namespaces for isolation between snapshot and host filesystems; it does not sandbox the dispatched binary against user privilege.
- **Not a build system.** Field dispatches what's already on the snapshot. Building from source is a separate move.
- **Not a substitute for bubble.** Python tools belong to bubble; field stays at the OS-binary layer. The two compose at field's Stage 1.5 (Python-shebang scripts delegated to `bubble run`).

## license

MIT
