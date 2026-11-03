"""Regression checks for version-scoped notes and immutable delivery gates."""

import importlib.util
import hashlib
import json
from pathlib import Path
import re
import struct
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


upstream = load("upstream", "upstream.py")
guard = load("guard", "verify-package.py")


class ReleaseNotesTests(unittest.TestCase):
    def test_only_exact_version_and_no_hydration_duplicates(self):
        html = '<h2>0.17.1</h2><p>outside</p><main><h2><span>0.17.1</span></h2><p>中文<b>修复</b></p><ul><li>保存笔记</li></ul><h2>0.17.0</h2><p>previous version</p></main><script>0.17.1 hydration</script>'
        result = upstream.release_notes(html, "0.17.1")
        self.assertIn("中文修复", result)
        self.assertIn("- 保存笔记", result)
        self.assertNotIn("previous", result)
        self.assertNotIn("outside", result)
        self.assertNotIn("hydration", result)

    def test_missing_duplicate_and_empty_versions_fail_closed(self):
        for html in ('<main><h2>0.17.0</h2><p>older</p></main>', '<main><h2>0.17.1</h2><h2>0.17.0</h2><p>older</p></main>', '<main><h2>0.17.1</h2><p>a</p><h2>0.17.1</h2><p>b</p></main>'):
            with self.subTest(html=html), self.assertRaises(ValueError):
                upstream.release_notes(html, "0.17.1")


class PackageGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / ".lazycat-action").mkdir()
        self.digest = "sha256:" + "a" * 64
        self.proof = {"source_version": "0.17.1", "application_version": "0.17.1", "digest": self.digest, "baseline_source_version": "0.7.1", "checks": guard.CHECKS}
        (self.root / 'content').mkdir()
        content = (ROOT / 'content/prepare-runtime.mjs').read_bytes()
        (self.root / 'content/prepare-runtime.mjs').write_bytes(content)
        self.proof['runtime_patch_sha256'] = hashlib.sha256(content).hexdigest()
        # Real runs write an upstream pin back to the repository. Replace it
        # in the isolated fixture so tests remain valid after publication.
        current = re.sub(r'^\s*# upstream: \S+\s*$', '', (ROOT / 'lzc-manifest.yml').read_text(), flags=re.MULTILINE)
        self.manifest = current + f"\n    # upstream: docker.io/aidenzou/i-notebook:0.17.1@{self.digest}\n"
        (self.root / "package.yml").write_text("version: 0.17.1\n")

    def check(self):
        (self.root / ".lazycat-action/upgrade-proof.json").write_text(json.dumps(self.proof))
        (self.root / "lzc-manifest.yml").write_text(self.manifest)
        guard.verify(self.root)

    def test_matching_tested_candidate_passes(self):
        self.check()

    def test_tag_race_is_rejected(self):
        self.manifest = self.manifest.replace(self.digest, "sha256:" + "b" * 64)
        with self.assertRaisesRegex(ValueError, "tested digest"):
            self.check()

    def test_missing_upgrade_checks_are_rejected(self):
        self.proof["checks"] = ["sqlite"]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            self.check()

    def test_renaming_database_is_rejected(self):
        self.manifest = self.manifest.replace("file:/appdata/db.sqlite", "file:/appdata/database.sqlite")
        with self.assertRaisesRegex(ValueError, "Missing persistence"):
            self.check()

    def test_runtime_icon_is_real_512px_png(self):
        data = (ROOT / "icon.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", data[16:24]), (512, 512))

    def test_untested_runtime_helper_is_rejected(self):
        (self.root / 'content/prepare-runtime.mjs').write_text('// changed after testing')
        with self.assertRaisesRegex(ValueError, 'compatibility helper'):
            self.check()


if __name__ == "__main__":
    unittest.main()
