"""Unit tests for the resolve + scope + dedup-by-content flow.

Hermetic: each test creates a fresh FIELD_HOME tempdir and writes a
synthetic index TSV. No real binaries dispatched (that's
test_dispatch_smoke.py).
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


class ResolveScopeTests(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="field-test-"))
        # Mutate the loaded config module's attributes in place — submodules
        # imported `from . import config` and share the module reference,
        # so this propagates without reload.
        from field import config
        config.FIELD_HOME = self.home
        config.SNAPSHOTS_DIR = self.home / "snapshots"
        config.INDEX_FILE = self.home / "index.tsv"
        config.LINEAGE_FILE = self.home / "lineage.tsv"
        config.HOST_FILE = self.home / "host.toml"
        config.SCOPES_FILE = self.home / "scopes.toml"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)
        os.environ.pop("FIELD_HOME", None)

    # ─── helpers ───
    def _write_index(self, rows):
        from field import config
        config.ensure_dirs()
        with open(config.INDEX_FILE, "w") as f:
            f.write("snapshot\tname\tabspath\tmode\tsha256\tsize\n")
            for r in rows:
                f.write("\t".join(str(c) for c in r) + "\n")

    # ─── tests ───
    def test_single_candidate_no_prompt(self):
        from field import resolve
        self._write_index([
            ("snap1", "tool", "/usr/bin/tool", "dynamic", "abc123", 1000),
        ])
        r = resolve.resolve("tool", Path("/tmp"), interactive=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.snapshot, "snap1")
        self.assertEqual(r.source, "single")

    def test_dedup_by_content_collapses_compat_symlinks(self):
        """Two paths, same sha256 — should collapse to ONE candidate."""
        from field import resolve
        self._write_index([
            ("snap1", "tool", "/usr/bin/tool", "dynamic", "abc123", 1000),
            ("snap1", "tool", "/bin/tool", "dynamic", "abc123", 1000),
        ])
        r = resolve.resolve("tool", Path("/tmp"), interactive=False)
        self.assertIsNotNone(r)
        # The non-interactive branch picks the first; key is that we
        # didn't have to prompt or call out >1 candidates.
        self.assertEqual(r.source, "single")

    def test_two_distinct_contents_route_to_default_when_noninteractive(self):
        """Different sha256 → 2 real candidates. Non-interactive resolves
        deterministically to the first."""
        from field import resolve
        self._write_index([
            ("snap1", "tool", "/usr/bin/tool", "dynamic", "aaa111", 1000),
            ("snap2", "tool", "/usr/bin/tool", "dynamic", "bbb222", 1100),
        ])
        r = resolve.resolve("tool", Path("/tmp"), interactive=False)
        self.assertIsNotNone(r)
        self.assertEqual(r.source, "default")

    def test_scope_pin_takes_precedence(self):
        """A scope rule for (name, cwd-prefix) wins over multi-candidate."""
        from field import resolve, scope
        self._write_index([
            ("snap1", "tool", "/usr/bin/tool", "dynamic", "aaa111", 1000),
            ("snap2", "tool", "/usr/bin/tool", "dynamic", "bbb222", 1100),
        ])
        proj = self.home / "projects" / "x"
        proj.mkdir(parents=True)
        scope.pin("tool", "snap2", "/usr/bin/tool", proj)

        # Inside the scoped subtree → scope rule wins.
        r = resolve.resolve("tool", proj, interactive=False)
        self.assertEqual(r.snapshot, "snap2")
        self.assertEqual(r.source, "scope")

        # Outside the scoped subtree → falls through to default.
        r = resolve.resolve("tool", Path("/tmp"), interactive=False)
        self.assertEqual(r.source, "default")

    def test_scope_longest_prefix_wins(self):
        from field import resolve, scope
        self._write_index([
            ("snap1", "tool", "/usr/bin/tool", "dynamic", "aaa111", 1000),
            ("snap2", "tool", "/usr/bin/tool", "dynamic", "bbb222", 1100),
        ])
        outer = self.home / "projects"
        inner = outer / "deep" / "nested"
        inner.mkdir(parents=True)
        scope.pin("tool", "snap1", "/usr/bin/tool", outer)
        scope.pin("tool", "snap2", "/usr/bin/tool", inner)

        # Inside `inner` → inner rule wins (longer prefix).
        r = resolve.resolve("tool", inner, interactive=False)
        self.assertEqual(r.snapshot, "snap2")
        # In `outer` but outside `inner` → outer rule wins.
        r = resolve.resolve("tool", outer, interactive=False)
        self.assertEqual(r.snapshot, "snap1")

    def test_unpin(self):
        from field import scope
        cwd = self.home / "x"
        cwd.mkdir()
        scope.pin("tool", "snap1", "/usr/bin/tool", cwd)
        scope.pin("other", "snap2", "/usr/bin/other", cwd)

        n = scope.unpin("tool", cwd)
        self.assertEqual(n, 1)
        rules = scope.all_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].name, "other")

    def test_substrate_probe_records_bwrap_reason(self):
        from field import substrate
        menu = substrate.probe()
        substrate.write_host_portrait(menu)
        # On any host: probe writes a portrait we can read back.
        rt = substrate.read_host_portrait()
        self.assertIsNotNone(rt)
        self.assertEqual(rt.bwrap, menu.bwrap)
        self.assertTrue(rt.ld_library_path)
        self.assertTrue(rt.direct)
        # If bwrap is unavailable, the reason is preserved.
        if not menu.bwrap:
            self.assertTrue(rt.bwrap_reason)

    def test_best_substrate_falls_back_when_top_unavailable(self):
        from field.substrate import SubstrateMenu, best_substrate
        menu = SubstrateMenu(bwrap=False, proot=False,
                              ld_library_path=True, direct=True)
        self.assertEqual(best_substrate(menu), "ld_library_path")

        menu_full = SubstrateMenu(bwrap=True, proot=True,
                                   ld_library_path=True, direct=True)
        self.assertEqual(best_substrate(menu_full), "bwrap")
        self.assertEqual(best_substrate(menu_full, requested="proot"), "proot")


if __name__ == "__main__":
    unittest.main(verbosity=2)
