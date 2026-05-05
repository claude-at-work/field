"""Substrate ladder for OS-binary dispatch.

Ports the shape of `bubble/route.py`'s SUBSTRATE_LADDER to the OS-binary
layer. Each substrate is a tier in the isolation/portability tradeoff:

    bwrap > proot > ld_library_path > direct

`bwrap` (full mount-namespace) is the canonical top. Where it's available,
the dispatched binary sees a /usr, /lib, /etc rooted at the snapshot,
isolated from the host.

`proot` (userspace path translation) is a slower second tier that works
in restricted environments (Termux, some containers) where unprivileged
user namespaces aren't allowed. Not shipped in Stage 1; placeholder.

`ld_library_path` is a no-isolation fallback: exec the snapshot binary
directly with LD_LIBRARY_PATH pointing into the snapshot's lib trees.
The binary reads the snapshot's libraries but interacts with the host's
/etc, /home, /proc, /dev, etc. — the user gets dispatch, not isolation.

`direct` (Stage 0) is for static binaries that don't need any
environment manipulation. Lowest cost, narrowest applicability.

The probe writes a host portrait to `~/.field/host.toml` recording which
substrates this kernel can host. The dispatcher picks the highest tier
available; downgrades are recorded as a `substrate_downgraded` failure
in the same self-portrait, matching `bubble/host.py:FAILURE_KINDS`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config


SUBSTRATE_LADDER = ("bwrap", "proot", "ld_library_path", "direct")


@dataclass
class SubstrateMenu:
    """What this host can run."""
    bwrap: bool = False
    proot: bool = False
    ld_library_path: bool = True       # always available — it's just env vars
    direct: bool = True                 # always available — it's just exec
    bwrap_reason: str = ""              # if bwrap unavailable, why


def probe() -> SubstrateMenu:
    """Detect substrate availability on this host.

    Cheap, repeatable; called once at index time and cached. The negative
    result for bwrap carries its reason — same posture as bubble's host
    portrait, so the operator sees what's missing, not just that it is.
    """
    menu = SubstrateMenu()

    if shutil.which("bwrap"):
        try:
            r = subprocess.run(
                ["bwrap", "--unshare-user", "--ro-bind", "/usr", "/usr",
                 "--proc", "/proc", "/usr/bin/true"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                menu.bwrap = True
            else:
                menu.bwrap_reason = (r.stderr or "non-zero exit").strip().splitlines()[0][:200]
        except (subprocess.TimeoutExpired, OSError) as exc:
            menu.bwrap_reason = f"{type(exc).__name__}: {exc}"
    else:
        menu.bwrap_reason = "bwrap not on PATH"

    menu.proot = bool(shutil.which("proot"))

    return menu


def best_substrate(menu: SubstrateMenu, requested: Optional[str] = None) -> str:
    """Pick the highest-tier substrate this host supports.

    If `requested` is set, return that substrate iff it's available;
    otherwise return the highest available below it. Mirrors the shape
    of bubble's `route.route()` without the history-consultation step
    (which lands when host.toml writes are wired in)."""
    available = {
        "bwrap": menu.bwrap, "proot": menu.proot,
        "ld_library_path": menu.ld_library_path, "direct": menu.direct,
    }
    if requested and available.get(requested):
        return requested
    start = SUBSTRATE_LADDER.index(requested) if requested in SUBSTRATE_LADDER else 0
    for s in SUBSTRATE_LADDER[start:]:
        if available.get(s):
            return s
    return "direct"


# ───────────────────────── dispatchers ─────────────────────────


def dispatch_direct(target: Path, argv: list[str], *,
                    capture_stderr: bool = False) -> tuple[int, str]:
    """Stage 0's path. For statically-linked binaries; no env munging.
    Returns (exit_code, captured_stderr) — same shape as the other
    dispatchers so the fault loop can call any of them uniformly."""
    if not capture_stderr:
        pid = os.fork()
        if pid == 0:
            try:
                os.execv(str(target), argv)
            except OSError as exc:
                sys.stderr.write(f"field: exec failed: {exc}\n")
                os._exit(127)
        _, status = os.waitpid(pid, 0)
        return _exit_from_status(status), ""
    proc = subprocess.run([str(target), *argv[1:]],
                          capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode, proc.stderr or ""


def _build_ld_path(snapshot_root: Path, extra_lib_dirs: tuple = ()) -> str:
    """Compose LD_LIBRARY_PATH from the default snapshot lib roots, the
    fault-loop's accumulated extras, and any host LD_LIBRARY_PATH."""
    libs = []
    for d in config.LIB_DIRS:
        p = snapshot_root / d
        if p.is_dir():
            libs.append(str(p))
    for extra in extra_lib_dirs:
        s = str(extra)
        if s not in libs:
            libs.append(s)
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    return ":".join(libs + ([existing] if existing else []))


def dispatch_ld_library_path(snapshot_root: Path, target: Path,
                             argv: list[str], *,
                             extra_lib_dirs: tuple = (),
                             capture_stderr: bool = False) -> tuple[int, str]:
    """No-isolation fallback. LD_LIBRARY_PATH points at the snapshot's
    library trees; everything else (/etc, /home, /proc) comes from host.

    Works in environments that forbid bwrap (Termux/proot, some
    containers). The binary sees a confused world — its libs are the
    snapshot's, its config is the host's — but it runs.

    Returns (exit_code, captured_stderr). When capture_stderr=False,
    stderr passes through to the parent's tty and the second tuple
    element is empty; the fault loop calls again with capture=True on
    failure to inspect ld.so output."""
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = _build_ld_path(snapshot_root, extra_lib_dirs)
    if not capture_stderr:
        pid = os.fork()
        if pid == 0:
            try:
                os.execve(str(target), argv, env)
            except OSError as exc:
                sys.stderr.write(f"field: exec failed: {exc}\n")
                os._exit(127)
        _, status = os.waitpid(pid, 0)
        return _exit_from_status(status), ""
    proc = subprocess.run([str(target), *argv[1:]], env=env,
                          capture_output=True, text=True)
    # Print stdout to parent so the user still sees output even on capture path.
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    return proc.returncode, proc.stderr or ""


def dispatch_bwrap(snapshot_root: Path, target: Path, argv: list[str], *,
                   extra_lib_dirs: tuple = (),
                   capture_stderr: bool = False) -> tuple[int, str]:
    """Full mount-namespace isolation via bwrap. The dispatched binary
    sees /usr, /lib, /etc, /usr/share rooted at the snapshot, while
    /home, /tmp, /proc, /sys, /dev, $HOME come from the host shared rw.

    `extra_lib_dirs` are additional directories from the fault loop —
    each becomes an additional --ro-bind so the in-namespace dynamic
    linker can find them. The dirs are bind-mounted at their original
    in-snapshot paths so they're discoverable on the standard
    library search path.
    """
    bwrap_argv = ["bwrap", "--unshare-user", "--unshare-pid",
                  "--die-with-parent",
                  "--proc", "/proc", "--dev", "/dev"]
    for d in config.BIND_DIRS:
        src = snapshot_root / d
        if src.is_dir():
            bwrap_argv += ["--ro-bind", str(src), "/" + d]
    for extra in extra_lib_dirs:
        if extra.is_dir() and snapshot_root in extra.parents:
            in_ns = "/" + str(extra.relative_to(snapshot_root))
            bwrap_argv += ["--ro-bind", str(extra), in_ns]
    home = os.environ.get("HOME") or "/root"
    if Path(home).is_dir():
        bwrap_argv += ["--bind", home, home]
    bwrap_argv += ["--bind", "/tmp", "/tmp"]
    if Path("/sys").is_dir():
        bwrap_argv += ["--ro-bind", "/sys", "/sys"]

    in_ns_target = "/" + str(target.relative_to(snapshot_root))
    bwrap_argv += [in_ns_target] + argv

    if capture_stderr:
        proc = subprocess.run(bwrap_argv, capture_output=True, text=True)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        return proc.returncode, proc.stderr or ""
    proc = subprocess.run(bwrap_argv)
    return proc.returncode, ""


def dispatch(substrate: str, snapshot_root: Path, target: Path,
             argv: list[str], *,
             extra_lib_dirs: tuple = (),
             capture_stderr: bool = False) -> tuple[int, str]:
    """Dispatch by substrate name. Returns (exit_code, captured_stderr).

    `extra_lib_dirs` is the fault loop's accumulated set of directories
    that need to be visible to the dynamic linker. Each substrate
    interprets it differently (LD_LIBRARY_PATH for ld_library_path,
    extra --ro-bind for bwrap, ignored for direct).
    """
    if substrate == "bwrap":
        return dispatch_bwrap(snapshot_root, target, argv,
                              extra_lib_dirs=extra_lib_dirs,
                              capture_stderr=capture_stderr)
    if substrate == "ld_library_path":
        return dispatch_ld_library_path(snapshot_root, target, argv,
                                        extra_lib_dirs=extra_lib_dirs,
                                        capture_stderr=capture_stderr)
    if substrate == "direct":
        return dispatch_direct(target, argv, capture_stderr=capture_stderr)
    raise ValueError(f"unknown / unimplemented substrate: {substrate!r}")


def _exit_from_status(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return -1


# ───────────────────────── host portrait ─────────────────────────


def write_host_portrait(menu: SubstrateMenu) -> None:
    """Write ~/.field/host.toml — same self-portrait shape bubble's
    host.py uses, scaled down. Records what substrates this machine can
    host so subsequent runs don't re-probe."""
    config.ensure_dirs()
    lines = [
        "# field host portrait — written by `field probe`",
        "",
        "[substrates]",
        f"bwrap = {str(menu.bwrap).lower()}",
        f"proot = {str(menu.proot).lower()}",
        f"ld_library_path = {str(menu.ld_library_path).lower()}",
        f"direct = {str(menu.direct).lower()}",
    ]
    if menu.bwrap_reason and not menu.bwrap:
        lines += ["", "[unavailable]", f'bwrap = "{menu.bwrap_reason}"']
    config.HOST_FILE.write_text("\n".join(lines) + "\n")


def read_host_portrait() -> Optional[SubstrateMenu]:
    """Reverse of write_host_portrait. Returns None if no portrait yet."""
    if not config.HOST_FILE.exists():
        return None
    menu = SubstrateMenu(ld_library_path=False, direct=False)
    section = None
    for line in config.HOST_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"')
        if section == "substrates":
            setattr(menu, k, v.lower() == "true")
        elif section == "unavailable" and k == "bwrap":
            menu.bwrap_reason = v
    return menu
