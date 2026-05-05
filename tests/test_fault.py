"""Unit tests for fault.py — the OS-binary error-loop primitives.

Covers parser, snapshot lib search, resolution cache, and host-portrait
fault recording. The end-to-end loop integration is exercised in
test_fault_loop.py via a monkey-patched dispatcher.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class FaultParseTests(unittest.TestCase):

    def test_parse_glibc_ld_error(self):
        from field import fault
        stderr = ("/usr/bin/git: error while loading shared libraries: "
                  "libpcre2-8.so.0: cannot open shared object file: "
                  "No such file or directory\n")
        self.assertEqual(fault.parse_ld_so_error(stderr), "libpcre2-8.so.0")

    def test_parse_glibc_with_version_suffix(self):
        from field import fault
        stderr = ("error while loading shared libraries: libssl.so.3: "
                  "cannot open shared object file\n")
        self.assertEqual(fault.parse_ld_so_error(stderr), "libssl.so.3")

    def test_parse_musl_format(self):
        from field import fault
        stderr = ("Error loading shared library libfoo.so.7: "
                  "No such file or directory (needed by /bin/foo)\n")
        self.assertEqual(fault.parse_ld_so_error(stderr), "libfoo.so.7")

    def test_parse_returns_none_for_unrelated_stderr(self):
        from field import fault
        self.assertIsNone(fault.parse_ld_so_error(""))
        self.assertIsNone(fault.parse_ld_so_error(
            "Permission denied\n"
            "Connection refused\n"
        ))


class FindLibTests(unittest.TestCase):

    def setUp(self):
        self.snap = Path(tempfile.mkdtemp(prefix="field-snap-"))
        (self.snap / "usr" / "lib" / "aarch64-linux-gnu").mkdir(parents=True)
        (self.snap / "usr" / "lib" / "aarch64-linux-gnu" / "blas").mkdir()
        (self.snap / "opt" / "vendor" / "lib").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.snap, ignore_errors=True)

    def test_finds_lib_in_standard_arch_dir(self):
        from field import fault
        target = self.snap / "usr" / "lib" / "aarch64-linux-gnu" / "libfoo.so.1"
        target.write_bytes(b"\x7fELF fake")
        found = fault.find_lib_in_snapshot(self.snap, "libfoo.so.1")
        self.assertEqual(found, target.parent)

    def test_finds_lib_in_arch_subdir(self):
        from field import fault
        target = self.snap / "usr" / "lib" / "aarch64-linux-gnu" / "blas" / "libblas.so.3"
        target.write_bytes(b"\x7fELF fake")
        found = fault.find_lib_in_snapshot(self.snap, "libblas.so.3")
        self.assertEqual(found, target.parent)

    def test_finds_lib_in_opt(self):
        from field import fault
        target = self.snap / "opt" / "vendor" / "lib" / "libvendor.so.2"
        target.write_bytes(b"\x7fELF fake")
        found = fault.find_lib_in_snapshot(self.snap, "libvendor.so.2")
        self.assertEqual(found, target.parent)

    def test_returns_none_when_lib_absent(self):
        from field import fault
        self.assertIsNone(fault.find_lib_in_snapshot(self.snap, "libnope.so.99"))


class CacheAndRecordTests(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="field-test-"))
        from field import config
        config.FIELD_HOME = self.home
        config.HOST_FILE = self.home / "host.toml"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_cache_resolution_roundtrip(self):
        from field import fault
        d1 = Path("/some/snap/opt/vendor/lib")
        d2 = Path("/some/snap/usr/lib/aarch64-linux-gnu/blas")
        fault.cache_resolution("abc123", "kali-fs", d1)
        fault.cache_resolution("abc123", "kali-fs", d2)
        fault.cache_resolution("abc123", "kali-fs", d1)  # dup
        # Different binary
        fault.cache_resolution("xyz789", "kali-fs", d1)

        out = fault.cached_extra_dirs("abc123", "kali-fs")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], d1)
        self.assertEqual(out[1], d2)

        out2 = fault.cached_extra_dirs("xyz789", "kali-fs")
        self.assertEqual(out2, [d1])

        # Snapshot scoping — same sha, different snapshot, no entries
        self.assertEqual(fault.cached_extra_dirs("abc123", "other-snap"), [])

    def test_record_fault_appends_to_host_toml(self):
        from field import fault
        fault.record_fault("library_missing_in_snapshot", "git@kali-fs",
                           detail="lib=libnope.so.1")
        fault.record_fault("dispatch_succeeded_after_fault", "vim@kali-fs",
                           detail="extras=['/snap/opt/vendor/lib']")
        failures = fault.known_failures()
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["kind"], "library_missing_in_snapshot")
        self.assertEqual(failures[0]["target"], "git@kali-fs")
        self.assertIn("libnope.so.1", failures[0]["detail"])
        self.assertEqual(failures[1]["kind"], "dispatch_succeeded_after_fault")


if __name__ == "__main__":
    unittest.main(verbosity=2)
