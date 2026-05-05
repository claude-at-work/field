"""Minimal ELF reader — enough to ask 'is this binary statically linked?'.

Static = no PT_INTERP program header. That's the only check we need at
stage 0. Anything fancier (PIE detection, DT_NEEDED enumeration) is for
the dynamic substrate at stage 1.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Optional


PT_INTERP = 3


def is_static_elf(path: Path) -> Optional[bool]:
    """True if static ELF, False if dynamic ELF, None if not ELF / unreadable."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return None
    if len(head) < 64 or head[:4] != b"\x7fELF":
        return None
    ei_class = head[4]
    if ei_class == 2:           # 64-bit
        e_phoff = struct.unpack_from("<Q", head, 32)[0]
        e_phentsize = struct.unpack_from("<H", head, 54)[0]
        e_phnum = struct.unpack_from("<H", head, 56)[0]
    elif ei_class == 1:         # 32-bit
        e_phoff = struct.unpack_from("<I", head, 28)[0]
        e_phentsize = struct.unpack_from("<H", head, 42)[0]
        e_phnum = struct.unpack_from("<H", head, 44)[0]
    else:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(e_phoff)
            ph = f.read(e_phentsize * e_phnum)
    except OSError:
        return None
    if len(ph) < e_phentsize * e_phnum:
        return None
    for i in range(e_phnum):
        p_type = struct.unpack_from("<I", ph, i * e_phentsize)[0]
        if p_type == PT_INTERP:
            return False
    return True
