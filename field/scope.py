"""Scope memory — `(name, cwd-prefix) → (snapshot, abspath)` pinning.

Mirrors the shape of bubble's `meta_finder._scope`: when the user has
multiple candidates for a binary, ask once, remember the answer keyed
by where they were standing when they answered.

Stored at ~/.field/scopes.toml as a list of [[scope]] entries:

    [[scope]]
    name = "python3"
    cwd_prefix = "/root/projects/firmament"
    snapshot = "kali-march-2026"
    abspath = "/usr/bin/python3"
    pinned_at = "2026-05-05T12:34:56"

cwd-prefix matches by `Path.is_relative_to`. The longest-prefix match
wins where multiple rules cover the same cwd.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config


@dataclass
class ScopeRule:
    name: str
    cwd_prefix: str
    snapshot: str
    abspath: str
    pinned_at: str


def _read_all() -> list[ScopeRule]:
    if not config.SCOPES_FILE.exists():
        return []
    rules: list[ScopeRule] = []
    cur: dict = {}
    in_scope = False
    for line in config.SCOPES_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[scope]]":
            if cur:
                rules.append(_rule_from_dict(cur))
                cur = {}
            in_scope = True
            continue
        if line.startswith("["):
            in_scope = False
            continue
        if not in_scope or "=" not in line:
            continue
        k, _, v = line.partition("=")
        cur[k.strip()] = v.strip().strip('"')
    if cur:
        rules.append(_rule_from_dict(cur))
    return rules


def _rule_from_dict(d: dict) -> ScopeRule:
    return ScopeRule(
        name=d.get("name", ""),
        cwd_prefix=d.get("cwd_prefix", ""),
        snapshot=d.get("snapshot", ""),
        abspath=d.get("abspath", ""),
        pinned_at=d.get("pinned_at", ""),
    )


def _write_all(rules: list[ScopeRule]) -> None:
    config.ensure_dirs()
    lines = ["# field scope rules — pinned binary choices by cwd prefix", ""]
    for r in rules:
        lines += [
            "[[scope]]",
            f'name = "{r.name}"',
            f'cwd_prefix = "{r.cwd_prefix}"',
            f'snapshot = "{r.snapshot}"',
            f'abspath = "{r.abspath}"',
            f'pinned_at = "{r.pinned_at}"',
            "",
        ]
    config.SCOPES_FILE.write_text("\n".join(lines).rstrip() + "\n")


def lookup(name: str, cwd: Path) -> Optional[ScopeRule]:
    """Find the scope rule that applies to (name, cwd). Longest matching
    cwd_prefix wins; None if no rule matches."""
    cwd = cwd.resolve()
    best: Optional[ScopeRule] = None
    best_len = -1
    for r in _read_all():
        if r.name != name:
            continue
        prefix = Path(r.cwd_prefix)
        try:
            if cwd.is_relative_to(prefix):
                if len(r.cwd_prefix) > best_len:
                    best, best_len = r, len(r.cwd_prefix)
        except (ValueError, AttributeError):
            continue
    return best


def pin(name: str, snapshot: str, abspath: str, cwd_prefix: Path) -> ScopeRule:
    """Record a pin. Replaces any existing rule for the same
    (name, cwd_prefix)."""
    rules = [r for r in _read_all()
             if not (r.name == name and Path(r.cwd_prefix) == cwd_prefix.resolve())]
    rule = ScopeRule(
        name=name, cwd_prefix=str(cwd_prefix.resolve()),
        snapshot=snapshot, abspath=abspath,
        pinned_at=datetime.now().isoformat(timespec="seconds"),
    )
    rules.append(rule)
    _write_all(rules)
    return rule


def unpin(name: str, cwd_prefix: Optional[Path] = None) -> int:
    """Remove pins. If cwd_prefix is None, removes all pins for this name.
    Returns count removed."""
    rules = _read_all()
    keep = []
    removed = 0
    for r in rules:
        if r.name == name and (
            cwd_prefix is None or Path(r.cwd_prefix) == cwd_prefix.resolve()
        ):
            removed += 1
            continue
        keep.append(r)
    if removed:
        _write_all(keep)
    return removed


def all_rules() -> list[ScopeRule]:
    return _read_all()
