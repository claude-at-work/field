"""Snapshot index — name → list of candidates.

TSV columns:
    snapshot  name  abspath  mode  sha256  size

mode is one of: static | dynamic | nonelf

A snapshot is just a directory tree. The 'name' is the basename; the
abspath is relative to the snapshot root (with leading slash) so the
record stays useful if the snapshot is later relocated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterator, NamedTuple

from . import config
from .elf import is_static_elf


_SHA_CHUNK = 1 << 20      # 1 MiB
_HASH_MAX_SIZE = 50 << 20  # skip hashing files >50 MiB at index time


class IndexRow(NamedTuple):
    snapshot: str
    name: str
    abspath: str           # relative to snapshot root, leading slash
    mode: str              # static | dynamic | nonelf
    sha256: str            # may be empty if file too large
    size: int


def _sha256(path: Path, max_size: int = _HASH_MAX_SIZE) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size > max_size:
        return ""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_SHA_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _classify(path: Path) -> str:
    static = is_static_elf(path)
    if static is None:
        return "nonelf"
    return "static" if static else "dynamic"


def scan_snapshot(snapshot_root: Path, snapshot_name: str) -> Iterator[IndexRow]:
    for rel in config.SCAN_DIRS:
        d = snapshot_root / rel
        if not d.is_dir():
            continue
        for entry in sorted(d.iterdir()):
            if entry.is_symlink() and not entry.exists():
                continue
            if not entry.is_file():
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            mode = _classify(entry)
            yield IndexRow(
                snapshot=snapshot_name,
                name=entry.name,
                abspath="/" + str(entry.relative_to(snapshot_root)),
                mode=mode,
                sha256=_sha256(entry),
                size=size,
            )


def write_index(rows: list[IndexRow]) -> None:
    config.ensure_dirs()
    tmp = config.INDEX_FILE.with_suffix(".tsv.tmp")
    with open(tmp, "w") as f:
        f.write("snapshot\tname\tabspath\tmode\tsha256\tsize\n")
        for r in rows:
            f.write(f"{r.snapshot}\t{r.name}\t{r.abspath}\t{r.mode}\t{r.sha256}\t{r.size}\n")
    tmp.replace(config.INDEX_FILE)


def read_index() -> list[IndexRow]:
    if not config.INDEX_FILE.exists():
        return []
    rows = []
    with open(config.INDEX_FILE) as f:
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 6:
                continue
            rows.append(IndexRow(
                snapshot=parts[0], name=parts[1], abspath=parts[2],
                mode=parts[3], sha256=parts[4], size=int(parts[5]),
            ))
    return rows


def candidates_for(name: str, mode_filter: str = "static") -> list[IndexRow]:
    return [r for r in read_index()
            if r.name == name and (mode_filter is None or r.mode == mode_filter)]
