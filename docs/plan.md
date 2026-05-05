# plan

The build order. Each stage proves a property the next depends on. Stages are not commitments; they're the order in which evidence arrives.

## stage 0 — static-binary probe

**Proves**: the capture-resolve-log loop end-to-end on the easy half of the problem (statically-linked binaries, no namespace needed).

**Scope**:
- `field index <snapshot-root>` — walk `/usr/bin`, `/usr/sbin`, `/usr/local/bin`, `/bin`, `/sbin` of a snapshot, ELF-check each, classify static vs dynamic, hash, write `~/.field/index.tsv`.
- `field run NAME [args...]` — look up `NAME` in the index, filter to `mode=static`, exec. Log dispatch to `~/.field/lineage.tsv`.
- `field log [--tail N]` — pretty-print the lineage.
- Bash hook: `command_not_found_handle` re-execs `field run`.

**Out of scope**: dynamic binaries, mount namespaces, multi-candidate UX, error loop. Each is a later stage.

**Done when**: a missing static binary in the new root resolves to a snapshot binary on first try, runs, and shows up in the lineage log.

## stage 1 — dynamic-binary substrate via bwrap (with multi-candidate UX)

**Proves**: substrate ladder works at OS layer; `Decision`/`record_failure` shape ports from bubble's `route.py`; the dispatcher produces transparent-on-miss UX on the common case.

**User story + success criterion**: see `docs/stage1.md`. The story is the criterion the implementation answers to.

**Structural shift from the original plan**: multi-candidate UX (originally Stage 3) is folded into Stage 1. The user story makes this mandatory — punting it produces a dispatcher that silently picks the wrong `python3` the first time the user has two installed, which is the common case on a real machine. Scope-per-cwd-prefix lands at the same time.

**Scope**:
- Detect `mode=dynamic` from the index.
- `bwrap` invocation builder: bind-mount `<snapshot>/{usr/lib,usr/lib64,lib,lib64,usr/share,etc,usr/libexec}` ro into a fresh mount namespace; share `/home`, `/tmp`, `/proc`, `/sys`, `/dev`, `$HOME` rw from host.
- Multi-candidate interactive prompt: when index has >1 candidate (after content-hash dedup), show snapshot + last-seen-context, pick one.
- Scope memory at `~/.field/scopes.toml`, keyed by `(name, cwd-prefix)`. Subsequent invocations under the prefix skip the prompt.
- Lineage records `substrate=bwrap` plus the namespace argv hash.

**Out of stage 1**: fault-loop closure expansion (Stage 2), strace-based observation (Stage 4), pid/user/network-namespace isolation (only mount-namespace ships in Stage 1), selective per-binary bind-mount subsets (Stage 4 produces the data; Stage 1 mounts the whole snapshot lib tree).

**Done when**: the 5-step manual test in `stage1.md` passes end-to-end.

## stage 2 — fault loop for closure expansion

**Proves**: bubble's `runner.py:25` error-loop pattern ports.

**Scope**:
- Catch ld.so's "error while loading shared libraries: libX.so.Y" stderr.
- Catch missing-`/etc/foo` syscall failures (open(... ENOENT)) at common config paths.
- Augment the bind-mount set, retry, bounded by `max_retries`.
- Record each fault to `~/.field/host.toml` as `library_missing_in_snapshot` / `etc_dependency_unresolved`.

**Done when**: a dynamic binary that needs an unanticipated `/etc/X` resolves on the second try, with the fault recorded so subsequent runs short-circuit (the second-run-starts-smarter property).

## stage 3 — scope tooling (folded; was multi-candidate UX)

**Note**: the multi-candidate prompt + cwd-scope memory ship in Stage 1 (forced by the user story). Stage 3 is reduced to *scope tooling* on top of what Stage 1 ships:

- `field scope show` — list all pinned `(name, cwd-prefix) → (snapshot, abspath)` rows.
- `field scope unpin <name> [--cwd PATH]` — undo a pin.
- `field scope pin <name> <snapshot> [--cwd PATH]` — pin without going through a prompt (useful in scripts).
- Optional: pin-by-git-root instead of cwd-prefix, when a git root is detected.

**Done when**: a user can audit and edit their pin set without hand-editing `~/.field/scopes.toml`.

## stage 4 — strace-based closure observation

**Proves**: nothing certain — this is the closure-undecidability wall. Stages 0-3 produce a working system; this stage attempts to produce a manifest.

**Scope**:
- Per-invocation, gated by `field --observe` flag, run under `strace -f -e trace=openat,execve,connect`.
- Filter to `<snapshot>/`-prefixed paths.
- Append observations to `~/.field/closures/<binary-sha>.tsv`, keyed by binary content hash, not invocation.
- Closures accumulate across invocations.

**Done when**: a binary's closure file shows the union of files actually opened across N invocations. The file is a *covering observation*, not a closure. The README from this point names the limitation explicitly.

## stage 5 — projection (manifest → clean root)

**Proves**: bubble's `bundle.py` codec composes with field's lineage.

**Scope**:
- Read `~/.field/closures/`, distill a per-binary manifest: `{binary_path, lib_closure, etc_closure, share_closure, host_facts}`.
- Port `bubble/bundle.py` shape: tar.gz with `.field.bundle.toml` + the file-system subset observed in use.
- `field bundle <output.tar.gz>` and `field unbundle <input.tar.gz>` against a fresh root.

**Done when**: a fresh minimal root + the bundle = a working subset of the source machine, distilled from observed use rather than declared.

---

## what this plan does *not* claim

- Total reproducibility. Stage 4 is a covering observation; absence from the closure does not mean absence of dependency.
- Transparency for non-interactive shells. Stage 0's bash hook is interactive-only. The PATH-front-loading shim (notebook entry, swap column) addresses this and is its own optional stage.
- Multi-snapshot composition. The substrate is single-snapshot-per-namespace by construction.
