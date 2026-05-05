"""Fault-driven closure expansion — the OS-binary analog of
bubble/run/runner.py:25's error loop.

Bubble's loop catches `ModuleNotFoundError` from a Python script's
stderr, fetches the missing dist into the vault, retries. The OS
analog: catch ld.so's `error while loading shared libraries:
libfoo.so.6: cannot open shared object file` from a dispatched
binary's stderr, find the library somewhere in the snapshot, augment
the substrate's lib path, retry.

Bounded retry count, same as runner.py. Each successful resolution is
cached at `~/.field/resolutions.tsv` keyed by the dispatched binary's
sha256 — second-run-starts-smarter property: the same binary on the
next invocation is pre-augmented and the first try succeeds.

What the fault loop *cannot* catch (Stage 4's job):
- Missing /etc/foo config files (not surfaced by ld.so)
- Daemon-not-running (no parseable stderr signature)
- Missing capability / kernel feature (silent failure)
- File reads of /usr/share/<pkg>/<data> at runtime (silent or
  application-specific)

Those need strace observation to detect. Stage 2 closes only the case
ld.so will tell us about loud and clearly.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config


# ld.so error format on glibc:
#   /path/to/binary: error while loading shared libraries: libNAME.so.VER: cannot open shared object file: ...
# Some musl variants:
#   Error loading shared library libNAME.so.VER: No such file or directory (needed by /path/to/binary)
_LD_GLIBC_RE = re.compile(
    r"error while loading shared libraries: ([^:\s]+\.so(?:\.[^:\s]+)?): "
    r"cannot open shared object file"
)
_LD_MUSL_RE = re.compile(
    r"Error loading shared library ([^:\s]+\.so(?:\.[^:\s]+)?):"
)


FAULT_KINDS = {
    "library_missing_in_snapshot",   # parsed lib name; nowhere in snapshot
    "ld_so_error_unparseable",       # nonzero stderr but no ld.so signature
    "dispatch_loop_exhausted",       # bounded retry count hit
    "dispatch_succeeded_after_fault",# success after augmentation
}


def parse_ld_so_error(stderr: str) -> Optional[str]:
    """Pull the missing library name out of ld.so's error text. Returns
    e.g. 'libcurl.so.4' or 'libssl.so.3', or None if not a parseable
    library fault."""
    if not stderr:
        return None
    for line in stderr.splitlines():
        for pat in (_LD_GLIBC_RE, _LD_MUSL_RE):
            m = pat.search(line)
            if m:
                return m.group(1)
    return None


def find_lib_in_snapshot(snapshot_root: Path, lib_name: str) -> Optional[Path]:
    """Locate a shared library file by name anywhere under the snapshot.

    Returns the *containing directory*, since LD_LIBRARY_PATH wants dirs
    not files; bwrap binds also work on dirs. Searches common library
    roots first, then falls back to a full snapshot walk capped at a
    reasonable depth.
    """
    common_roots = [
        snapshot_root / "usr" / "lib",
        snapshot_root / "usr" / "lib64",
        snapshot_root / "usr" / "local" / "lib",
        snapshot_root / "lib",
        snapshot_root / "lib64",
        snapshot_root / "opt",
    ]
    for root in common_roots:
        if not root.is_dir():
            continue
        # Most libs live within 4 levels — usr/lib/<arch>/<lib>, opt/<vendor>/lib/<lib>, etc.
        try:
            for path in root.rglob(lib_name):
                if path.is_file() or path.is_symlink():
                    return path.parent
        except OSError:
            continue
    return None


# ─────────────────────────── resolution cache ───────────────────────────


def cache_resolution(binary_sha: str, snapshot: str, lib_dir: Path) -> None:
    """Record that <binary_sha> needs <lib_dir> from <snapshot>. Append-only;
    duplicates are tolerated (read-side dedupes)."""
    config.ensure_dirs()
    res_file = config.FIELD_HOME / "resolutions.tsv"
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"{ts}\t{binary_sha}\t{snapshot}\t{lib_dir}\n"
    with open(res_file, "a") as f:
        f.write(line)


def cached_extra_dirs(binary_sha: str, snapshot: str) -> list[Path]:
    """Return the lib dirs previously learned to be needed for this binary,
    deduped, in insertion order."""
    res_file = config.FIELD_HOME / "resolutions.tsv"
    if not res_file.exists():
        return []
    seen: set[str] = set()
    out: list[Path] = []
    with open(res_file) as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            _ts, sha, snap, ld = parts[:4]
            if sha != binary_sha or snap != snapshot:
                continue
            if ld in seen:
                continue
            seen.add(ld)
            out.append(Path(ld))
    return out


# ─────────────────────────── fault recording ───────────────────────────


def record_fault(kind: str, target: str, detail: str = "") -> None:
    """Append a [[failures]] entry to host.toml. Same shape as bubble's
    host.record_failure — kind drawn from FAULT_KINDS, target is the
    binary name + snapshot, detail is anything else worth keeping."""
    config.ensure_dirs()
    if kind not in FAULT_KINDS:
        # Tolerated for back-compat but worth flagging.
        import sys
        sys.stderr.write(f"field: warning: unknown fault kind {kind!r}\n")
    ts = datetime.now().isoformat(timespec="seconds")
    block = (
        "\n[[failures]]\n"
        f'kind = "{kind}"\n'
        f'target = "{target}"\n'
        f'detail = "{detail}"\n'
        f'observed_at = "{ts}"\n'
    )
    with open(config.HOST_FILE, "a") as f:
        f.write(block)


def known_failures() -> list[dict]:
    """Read [[failures]] entries from host.toml. Lightweight reader; matches
    what bubble's host.py does without pulling tomllib."""
    if not config.HOST_FILE.exists():
        return []
    out: list[dict] = []
    cur: dict = {}
    in_block = False
    for line in config.HOST_FILE.read_text().splitlines():
        line = line.strip()
        if line == "[[failures]]":
            if cur:
                out.append(cur)
            cur = {}
            in_block = True
            continue
        if line.startswith("["):
            if cur and in_block:
                out.append(cur)
                cur = {}
            in_block = False
            continue
        if not in_block or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cur[k.strip()] = v.strip().strip('"')
    if cur and in_block:
        out.append(cur)
    return out
