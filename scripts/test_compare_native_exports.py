# SPDX-License-Identifier: Apache-2.0
"""Synthetic mutations of retained export metadata; no build or workflow."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import compare_native_exports as compare

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence/p3/2026-10-22-native-private-export-pass"


def encode(rows):
    for name in compare.NAMES[2:]:
        raw = compare.export.encoded(rows[name])
        rows["manifest.json"]["receipts"][name] = {"bytes": len(raw), "sha256": compare.files.digest(raw)}
    return {name: compare.export.encoded(row) for name, row in rows.items()}


class CompareNativeExportsTests(unittest.TestCase):
    def setUp(self):
        self.left = {name: (EVIDENCE / name).read_bytes() for name in compare.NAMES}
        self.rows = {name: json.loads(raw) for name, raw in self.left.items()}
        self.rows["run.json"].update(started_utc="2026-10-22T18:00:00Z", ended_utc="2026-10-22T18:05:00Z")

    def test_matching_hashes_do_not_assert_builder_independence_or_confidentiality(self):
        result = compare.compare(self.left, encode(self.rows))
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["matches"], {"root.raw": True, "nib.efi": True, "verity_root": True})
        for key in ("independent_builders_verified", "boot_verified", "signed", "confidentiality_verified"):
            self.assertIs(result[key], False)

    def test_valid_different_artifact_is_a_recorded_failure_not_parse_error(self):
        # Keep all proof bindings coherent while changing one synthetic image.
        replacement = {"bytes": 4096, "sha256": "ab" * 32}
        for name in ("manifest.json", "validation.json"):
            self.rows[name]["files"]["nib.efi"] = replacement
        worker = self.rows["result.json"]["worker"]
        worker["artifact_stage"]["files"]["nib.efi"] = replacement
        worker["boot_uki"]["uki"]["image"] = replacement
        result = compare.compare(self.left, encode(self.rows))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["matches"]["nib.efi"])
        self.assertTrue(result["matches"]["root.raw"])

    def test_ci_expected_source_is_exact_and_does_not_change_the_receipt(self):
        right = encode(self.rows)
        expected = self.rows["manifest.json"]["source"]["git_sha"]
        self.assertEqual(compare.compare(self.left, right, expected_source=expected),
                         compare.compare(self.left, right))
        for invalid in ("ab" * 20, expected.upper(), expected[:7], "main", "", True, 1):
            with self.subTest(source=invalid), self.assertRaises(ValueError):
                compare.compare(self.left, right, expected_source=invalid)

    def test_cli_enforces_expected_source_and_preserves_failure_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for slot, raws in (("left", self.left), ("right", encode(self.rows))):
                directory = root / slot
                directory.mkdir()
                for name, raw in raws.items():
                    (directory / name).write_bytes(raw)
            expected = self.rows["manifest.json"]["source"]["git_sha"]
            command = [sys.executable, "-s", "-S", "-B", compare.__file__,
                       str(root / "left"), str(root / "right"), "--expected-source"]
            valid = subprocess.run(command + [expected], capture_output=True, check=True, timeout=5)
            self.assertEqual(json.loads(valid.stdout), compare.compare(self.left, encode(self.rows)))
            invalid = subprocess.run(command + ["ab" * 20], capture_output=True, timeout=5)
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual(invalid.stdout, b"")
            self.assertEqual(invalid.stderr, b"Native export comparison rejected; no qualification granted.\n")

    def test_copied_and_overlapping_runs_reject(self):
        with self.assertRaises(ValueError):
            compare.compare(self.left, self.left)
        self.rows["run.json"]["started_utc"] = "2026-10-22T17:52:00Z"
        with self.assertRaises(ValueError):
            compare.compare(self.left, encode(self.rows))

    def test_source_or_validator_mismatch_rejects(self):
        for kind in ("source", "validator"):
            with self.subTest(kind=kind):
                rows = copy.deepcopy(self.rows)
                if kind == "source":
                    for name in ("manifest.json", "validation.json"):
                        rows[name]["source"]["sources"]["scripts/mkosi_export.py"] = "ab" * 32
                    rows["result.json"]["worker"]["artifact_stage"]["source"]["sources"]["scripts/mkosi_export.py"] = "ab" * 32
                else:
                    rows["validation.json"]["validator_source"][compare.WORKFLOW] = "ab" * 32
                with self.assertRaises(ValueError):
                    compare.compare(self.left, encode(rows))

    def test_cleanup_failure_and_forged_qualifications_reject(self):
        mutations = [("resources.json", "empty_verified", False),
            ("validation.json", "private_images_removed", 1),
            ("manifest.json", "local_only", 1), ("run.json", "hardware_validated", True)]
        mutations += [(name, key, value) for name in ("manifest.json", "validation.json")
                      for key in compare.QUALIFICATIONS for value in (True, 0)]
        for name, key, value in mutations:
            with self.subTest(name=name, key=key, value=value):
                rows = copy.deepcopy(self.rows)
                rows[name][key] = value
                with self.assertRaises(ValueError):
                    compare.compare(self.left, encode(rows))

    def test_receipt_tampering_duplicate_keys_nonfinite_and_oversize_reject(self):
        for replacement in (self.left["run.json"] + b" ", b'{"x":1,"x":2}', b'{"x":NaN}',
                            b" " * (compare.export.MAX_JSON + 1)):
            raws = dict(self.left, **{"run.json": replacement})
            with self.assertRaises(ValueError):
                compare.validate(raws)

    def test_file_set_and_link_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name, raw in self.left.items():
                (root / name).write_bytes(raw)
            self.assertEqual(compare.read(root), self.left)
            (root / "extra").write_text("unexpected")
            with self.assertRaises(ValueError):
                compare.read(root)
            (root / "extra").unlink()
            (root / "run.json").unlink()
            (root / "run.json").symlink_to(EVIDENCE / "run.json")
            with self.assertRaises(OSError):
                compare.read(root)


if __name__ == "__main__":
    unittest.main()
