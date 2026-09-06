"""Verify that vendored UCP schemas exactly match the pinned lockfile."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest
from referencing.exceptions import Unresolvable

from schema_support import ucp_registry

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

    def test_every_vendored_reference_resolves_offline(self) -> None:
        """Ensure every recursive $ref is available from the frozen registry."""
        registry = ucp_registry()
        lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        vendor_root = ROOT / "vendor" / "ucp" / lock["commit"] / "source" / "schemas"

        def references(value):
            if isinstance(value, dict):
                if isinstance(value.get("$ref"), str):
                    yield value["$ref"]
                for child in value.values():
                    yield from references(child)
            elif isinstance(value, list):
                for child in value:
                    yield from references(child)

        for path in sorted(vendor_root.rglob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            resolver = registry.resolver(document["$id"])
            for reference in references(document):
                with self.subTest(path=path.name, reference=reference):
                    try:
                        resolver.lookup(reference)
                    except Unresolvable as error:
                        self.fail(f"unresolved vendored reference: {error}")


if __name__ == "__main__":
    unittest.main()
