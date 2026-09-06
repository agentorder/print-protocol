"""Verify that vendored UCP schemas exactly match the pinned lockfile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
LOCK_PATH = ROOT / "ucp.lock.json"


class VendorLockTest(unittest.TestCase):
    def test_vendor_tree_matches_lock(self) -> None:
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        commit = lock["commit"]
        vendor_root = ROOT / "vendor" / "ucp" / commit
        self.assertTrue(vendor_root.is_dir(), f"missing vendor root: {vendor_root}")

        actual = {
            str(path.relative_to(vendor_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(vendor_root.rglob("*"))
            if path.is_file()
        }
        expected = lock["files"]
        self.assertEqual(set(actual), set(expected), "vendored file set differs from lock")
        for relative_path, digest in actual.items():
            self.assertEqual(digest, expected[relative_path], f"hash mismatch: {relative_path}")


if __name__ == "__main__":
    unittest.main()
