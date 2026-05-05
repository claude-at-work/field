"""Append-only lineage log. One row per dispatch."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config


def record(name: str, snapshot: str, abspath: str, mode: str,
           argv: list[str], cwd: Path, exit_code: Optional[int],
           substrate: str = "direct") -> None:
    """Append one dispatch record. Columns:
        ts  cwd  name  snapshot  abspath  mode  substrate  exit  argv
    """
    config.ensure_dirs()
    ts = datetime.now().isoformat(timespec="seconds")
    argv_str = " ".join(a.replace("\t", " ").replace("\n", " ") for a in argv)
    ec = "" if exit_code is None else str(exit_code)
    line = (f"{ts}\t{cwd}\t{name}\t{snapshot}\t{abspath}\t{mode}"
            f"\t{substrate}\t{ec}\t{argv_str}\n")
    with open(config.LINEAGE_FILE, "a") as f:
        f.write(line)


def tail(n: int) -> list[str]:
    if not config.LINEAGE_FILE.exists():
        return []
    with open(config.LINEAGE_FILE) as f:
        return f.readlines()[-n:]
