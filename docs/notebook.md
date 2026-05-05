# notebook

A running log of what surfaces during `field` development that doesn't fit a commit message or a code comment. Four columns; entries are dated and signed.

The columns:

- **anomaly** — something that didn't happen the way the analogy or the spec suggested it would. Symptom + the moment it became legible.
- **hidden potential** — a capability the current scaffolding accidentally exposes that wasn't part of the spec. Worth reaching for, often cheap.
- **soft spot** — a barrier that looked solid in the design phase and turned out to be permeable, OR a barrier that looked permeable and turned out to be load-bearing. The places maps disagree with terrain.
- **swap** — a place where the convention should retire and novelty should take its seat, or where novelty was overreaching and the convention is actually right. Mark direction with `→ novel` or `→ convention`.

The discipline: write entries while building, not after. The reading from inside the work is the one that's hardest to recover later. Date in absolute form (YYYY-MM-DD); cite the file/line where the observation landed if it's code-shaped.

---

## seed entries — 2026-05-04

Entries from the architectural-mapping pass, before the first probe. Subject to revision once code disagrees.

### anomaly

- **The Python error loop is structurally luckier than it looks.**
  `runner.py:25` parses `ModuleNotFoundError` from stderr because Python's traceback format is a stable, parseable contract. ld.so's "error while loading shared libraries: libfoo.so.6" is *also* parseable, but the surrounding exit code (127) is shared with "binary not in PATH" and other things. The OS-layer fault loop will spend more LOC on disambiguating fault classes than on resolving them. (Where: Stage 2.)

- **`command_not_found_handle` doesn't fire from non-interactive shells.**
  Means `make`, `bash -c '...'`, `system(3)` from C programs all bypass the dispatcher. The trap is shell-personal, not user-personal. The honest spec is "transparent on miss in your interactive shell," not "transparent on miss." (Where: Stage 0 install hook.)

### hidden potential

- **The lineage TSV is a tooling surface before it's a manifest source.**
  Once it exists, you can `grep`, `awk`, `cut` over it. "What did I run last week" / "what binaries from snapshot-X have I never reached for in 60 days" / "show me every invocation of `gcc` and what tree it ran in." That's emergent diagnostic value the spec didn't promise. (Where: Stage 0.)

- **Bubble's `host.toml` `FAILURE_KINDS` vocabulary is portable to OS-layer faults with a small extension set.**
  `binary_not_indexed`, `static_check_failed`, `library_missing_in_snapshot`, `etc_dependency_unresolved`, `multi_candidate_unresolved`, `dispatcher_loop_exhausted`. The warp ports; the specific kinds extend the loom. (Where: Stage 1.)

### soft spot

- **The "snapshot as vault" analogy weakens at composability.**
  Two wheels merge in `site-packages`. Two snapshot `/usr/lib`s don't merge — they share filenames with different bytes. You can pick *one* snapshot per invocation, period. The substrate ladder for OS binaries is single-snapshot-per-namespace by construction; the multi-version coexistence story bubble has *does not port*. The dispatcher dispatches; it does not blend.

- **Closure undecidability is the real wall, not the trap mechanism.**
  Bubble's "reproducibility comes from observation, not declaration" is honest at the Python layer because `sys.modules` is exhaustive. At the OS layer, `strace`-based capture is invocation-specific, not binary-specific. `git status` and `git push --force-with-lease` open different files. The strongest honest claim is "covering observation," not "closure." (Where: Stage 4.)

- **Transparent-on-miss is opaque-on-hit.**
  If `python` exists in the new root, the dispatcher never fires; the user's muscle memory ("I expected snapshot's older python") gets silently different behavior. The system is transparent only at the boundary of what's *missing* from the new root. The boundary is invisible to the user without explicit signage.

### swap

- **bash `command_not_found_handle` → convention, but PATH-front-loading → novel.**
  The conventional move is the bash hook; it's brittle (interactive-only). The novel move is to put a shim directory at the *front* of `$PATH` populated with one stub-script per indexed snapshot binary. Each stub re-execs `field run NAME "$@"`. This catches non-interactive shells, `make`, `system(3)`. Cost: needs the index to know names a priori. Worth: every shell sees the same dispatcher.
  *Direction: → novel,* once the index is stable.

- **`bwrap` mount-namespace → convention is right.**
  Distrobox/toolbox/Nix already carry the load here. Don't reinvent. The novelty in `field` is the *retrofittable, lineage-aware, transparent* posture, not the namespace mechanism. Use bwrap; describe the posture.
  *Direction: → convention,* for the substrate.

- **`strace` for closure observation → starts as convention, may need novelty.**
  Strace is the obvious capture tool. But the per-syscall overhead and the volume of noise mean a real probe will want either (a) `bpftrace`/eBPF on the openat path with a pid-namespace filter, or (b) a `LD_AUDIT` library that hooks `dlopen`/`la_objsearch` and records to a unix socket. Both are more invasive but cheaper at runtime.
  *Direction: undetermined.* Start with strace; revisit when overhead bites.

---

## entries — 2026-05-04, after first probe

Stage 0 ran end-to-end against `/root/kali-fs` (1815 binaries scanned, dispatch works, lineage recorded). Findings the design phase did not anticipate:

### anomaly

- **Static binaries are a vanishing edge case on a desktop Linux fs.**
  Index pass: 1815 entries scanned, **4 static**, 1405 dynamic, 406 non-ELF (scripts, symlinks-to-relative-paths, data files in `bin/`). The 4 statics are `ld.so` and `ldconfig` (×2 each, in `/usr/sbin` and `/sbin`). They have to be static — they *are* the dynamic loader; `ld.so` cannot itself depend on `ld.so`. Everything a user actually types (`busybox`, `bash`, `python`, `git`) is dynamic. *The probe proves the mechanism. It does not prove utility — that has to wait for Stage 1.* (Where: `field/index.py:_classify`; observed at `/root/kali-fs` 2026-05-04.)

- **Multi-candidate triggers immediately on real data.**
  `ldconfig` appears at both `/usr/sbin/ldconfig` and `/sbin/ldconfig` in the snapshot. Same bytes (same sha256 once we add hashing dedup), but the Stage 3 UX trigger fired on the very first dispatch. Same shape will hit for everything that has a `/usr/bin` ↔ `/bin` compat symlink. The dedup-on-content-hash move is mandatory before Stage 3 prompts the user; otherwise the prompt is a no-op forced choice. (Where: `field/index.py:scan_snapshot` does not dedup; should group rows where `sha256` and `mode` match across abspaths.)

### hidden potential

- **The TSV index is already an audit surface.**
  After one `field index` pass: `awk -F'\t' '$4=="dynamic"{print $2}' ~/.field/index.tsv | sort -u | wc -l` → unique dynamic binary names available in the snapshot. That's "what would have access to in the new root if we had Stage 1 ready." The number is the user's accumulated CLI vocabulary, made legible. Worth a `field stats` command in Stage 0.5.

- **The non-ELF bucket is mostly scripts and is independently dispatchable.**
  406 entries in `mode=nonelf`. These are mostly `#!/usr/bin/env python3`, `#!/bin/sh`, etc. They need *only* their interpreter (which can be a bind-mount or a simple PATH redirect) — not a full library closure. A `mode=script` substrate is a separate, cheap third tier between static (no isolation needed) and dynamic (full bwrap). Worth its own classification at Stage 0.5.

### soft spot

- **The "static-first probe" choice was epistemically right and operationally wrong.**
  Static-first sidesteps the library-closure problem; that's why the probe is small. But static-first also means there's nothing on a real machine to dispatch except the loader itself, so the probe demonstrates the architecture without producing observable user value. The honest framing: *Stage 0 proves the dispatcher is alive; Stage 1 is where the dispatcher meets the user's intent.* The README/plan should not promise utility before Stage 1.

- **`os.fork()` in a Python entry-point that runs with `python3 -m field` triggers a brief warning under newer Pythons about thread-state.**
  Did not surface in this run, but Python 3.13's `os.fork()` policy is hardening. May need to switch to `subprocess.Popen` / `os.posix_spawn` for the dispatch path. Cheap to change; flag for Stage 1.

### swap

- **Dedup-by-content-hash → novel for the index, not convention.**
  Conventional file indexes (locate, mlocate, plocate) key on path. The novel move for `field`: key on `(name, sha256)`, and let the row carry a list of paths it lives at. Then a single binary that lives at `/usr/bin/X` and `/bin/X` is one *candidate*, two *paths*, never a "multi-candidate" prompt unless the contents actually differ. *Direction: → novel,* and earlier than I had it (Stage 0.5, not 3).

---

## entries — 2026-05-04, Stage 1 user-story pass

Tyler asked for the user story before code. Wrote `docs/stage1.md`. The story shifts the plan structurally; recording why.

### swap

- **Multi-candidate UX → from Stage 3 forward into Stage 1.**
  The original plan had it punted because the dispatch mechanism is the "real" Stage 1 work. The user story exposed why that's wrong: Stage 1 without multi-candidate produces a dispatcher that silently picks the wrong `python3` the first time the user has two installed, which is the *common case on real machines*, not an edge case. Punting it is the supporting-tissue drift named in `Bubblev2/docs/weft.md` — "I built the substrate, the UX is downstream." If the substrate ships without the UX it ships *broken from the user's perspective*. Forward.
  *Direction: → ahead,* not novel-vs-convention but earlier-vs-later.

### hidden potential

- **The user story doubles as the test plan.**
  `docs/stage1.md`'s "what counts as evidence" section is a 5-step manual test. Once Stage 1 ships, that script becomes the seed for `tests/test_stage1.py`. The story → criterion → test sequence is one artifact in three forms; writing the story carefully means the test writes itself.

### soft spot

- **"The user doesn't realize the binary came from a snapshot" is a UX promise the implementation can't fully keep.**
  bwrap-mounted binaries see a different `/etc`, a different `ldd`-equivalent view, a different `/usr/share`. Most user-facing behavior is identical, but a binary that introspects its own environment (`apt --version` checking sources.list, a tool reading `/etc/os-release`) will see the snapshot's reality, not the host's. The honest framing: *the user doesn't realize at the action level (typing the command and getting output)*. They will realize the moment they ask the binary about its environment. Worth naming in the README so the promise stays calibrated.
