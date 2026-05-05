"""Integration test for the fault loop in cli.cmd_run.

Synthesizes a minimal scenario: an indexed binary that 'fails' on first
dispatch with a parseable ld.so error, succeeds on second dispatch when
the missing lib's directory is added to the substrate's lib set. Asserts:
  - the loop fires exactly once
  - the augmentation gets cached, keyed by binary sha
  - subsequent invocations of the same binary are pre-augmented and
    succeed on the first try (the second-run-starts-smarter property)
  - on success after a fault, host.toml records dispatch_succeeded_after_fault
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class FaultLoopTests(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="field-test-"))
        self.snap = Path(tempfile.mkdtemp(prefix="field-snap-"))
        # Build a fake snapshot tree with the lib stashed in a subdir
        # the default LIB_DIRS doesn't cover.
        (self.snap / "usr" / "bin").mkdir(parents=True)
        bindir = self.snap / "opt" / "vendor" / "lib"
        bindir.mkdir(parents=True)
        (bindir / "libvendor.so.1").write_bytes(b"\x7fELF fake")

        # The "binary" is a sentinel file; our shim won't actually exec it.
        target = self.snap / "usr" / "bin" / "tool"
        target.write_bytes(b"\x7fELF fake binary contents for sha")

        # Mutate config in place — submodules grabbed `from . import config`
        # so they share the module object.
        from field import config
        config.FIELD_HOME = self.home
        config.SNAPSHOTS_DIR = self.home / "snapshots"
        config.SNAPSHOTS_DIR.mkdir(parents=True)
        os.symlink(self.snap, config.SNAPSHOTS_DIR / "kali-fs")
        config.INDEX_FILE = self.home / "index.tsv"
        config.LINEAGE_FILE = self.home / "lineage.tsv"
        config.HOST_FILE = self.home / "host.toml"
        config.SCOPES_FILE = self.home / "scopes.toml"
        config.ensure_dirs()

        # Build a one-row index
        import hashlib
        binary_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        self.binary_sha = binary_sha
        with open(config.INDEX_FILE, "w") as f:
            f.write("snapshot\tname\tabspath\tmode\tsha256\tsize\n")
            f.write(f"kali-fs\ttool\t/usr/bin/tool\tdynamic\t{binary_sha}\t{target.stat().st_size}\n")

        # Write a minimal host portrait so cmd_run reads it back.
        from field import substrate
        substrate.write_host_portrait(substrate.SubstrateMenu(
            bwrap=False, proot=False, ld_library_path=True, direct=True,
        ))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.snap, ignore_errors=True)

    def _patch_dispatch(self, scenarios: list):
        """Install a fake substrate.dispatch that returns canned
        (exit_code, stderr) pairs in order. Each call pops the next.
        Returns the call log."""
        from field import substrate
        calls = []
        original = substrate.dispatch

        def fake(substrate_name, snapshot_root, target, argv, *,
                 extra_lib_dirs=(), capture_stderr=False):
            calls.append({
                "substrate": substrate_name,
                "extra_lib_dirs": tuple(extra_lib_dirs),
                "capture_stderr": capture_stderr,
                "argv": tuple(argv),
            })
            if not scenarios:
                return (0, "")
            return scenarios.pop(0)

        substrate.dispatch = fake
        self.addCleanup(lambda: setattr(substrate, "dispatch", original))
        return calls

    def _run(self, name="tool", argv=()):
        from field import cli
        ns = argparse.Namespace(name=name, argv=list(argv))
        return cli.cmd_run(ns)

    def test_fault_loop_recovers_missing_lib(self):
        from field import fault
        ld_err = ("/usr/bin/tool: error while loading shared libraries: "
                  "libvendor.so.1: cannot open shared object file: "
                  "No such file or directory\n")
        # Sequence: first run (no capture) -> 127.
        # Re-run with capture -> 127 + stderr to parse.
        # After augmentation, dispatch -> 0 (no capture).
        calls = self._patch_dispatch([
            (127, ""),       # first try, no capture
            (127, ld_err),   # capture re-run
            (0, ""),         # retry after augmentation
        ])
        ec = self._run()
        self.assertEqual(ec, 0)

        # Verify the loop went through exactly the expected sequence.
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0]["capture_stderr"], False)
        self.assertEqual(calls[0]["extra_lib_dirs"], ())
        self.assertEqual(calls[1]["capture_stderr"], True)
        self.assertEqual(calls[1]["extra_lib_dirs"], ())
        # Third call had the augmentation
        self.assertEqual(calls[2]["capture_stderr"], False)
        self.assertEqual(len(calls[2]["extra_lib_dirs"]), 1)
        aug_dir = calls[2]["extra_lib_dirs"][0]
        self.assertEqual(aug_dir.name, "lib")
        self.assertEqual(aug_dir.parent.name, "vendor")

        # Verify the resolution was cached.
        cached = fault.cached_extra_dirs(self.binary_sha, "kali-fs")
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0], aug_dir)

        # And host.toml recorded the recovery.
        failures = fault.known_failures()
        self.assertTrue(any(f["kind"] == "dispatch_succeeded_after_fault"
                            for f in failures))

    def test_second_run_pre_augments_from_cache(self):
        """The second-run-starts-smarter property: after a successful
        fault recovery, the same binary on the next invocation should
        succeed on the *first* dispatch — pre-augmented from the cache."""
        from field import fault
        # Pre-seed the cache as if a previous run had recovered.
        fault.cache_resolution(self.binary_sha, "kali-fs",
                                self.snap / "opt" / "vendor" / "lib")
        calls = self._patch_dispatch([
            (0, ""),    # first try succeeds — no fault loop
        ])
        ec = self._run()
        self.assertEqual(ec, 0)
        self.assertEqual(len(calls), 1)
        # Pre-augmented from cache
        self.assertEqual(len(calls[0]["extra_lib_dirs"]), 1)
        self.assertEqual(calls[0]["extra_lib_dirs"][0].name, "lib")

    def test_unrecoverable_lib_records_and_returns_failure(self):
        """Lib named in stderr but absent from snapshot — record
        library_missing_in_snapshot and return the failure exit code."""
        from field import fault
        ld_err = ("/usr/bin/tool: error while loading shared libraries: "
                  "libnope.so.99: cannot open shared object file\n")
        self._patch_dispatch([
            (127, ""),
            (127, ld_err),
        ])
        ec = self._run()
        self.assertEqual(ec, 127)
        kinds = [f["kind"] for f in fault.known_failures()]
        self.assertIn("library_missing_in_snapshot", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
