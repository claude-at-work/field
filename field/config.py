"""Paths. ~/.field is the data root, mirroring ~/.bubble."""

from __future__ import annotations

import os
from pathlib import Path


FIELD_HOME = Path(os.environ.get("FIELD_HOME", os.path.expanduser("~/.field")))

SNAPSHOTS_DIR = FIELD_HOME / "snapshots"
INDEX_FILE = FIELD_HOME / "index.tsv"
LINEAGE_FILE = FIELD_HOME / "lineage.tsv"
HOST_FILE = FIELD_HOME / "host.toml"
SCOPES_FILE = FIELD_HOME / "scopes.toml"


SCAN_DIRS = ("usr/bin", "usr/sbin", "usr/local/bin", "bin", "sbin", "usr/local/sbin")


def ensure_dirs() -> None:
    for d in (FIELD_HOME, SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
