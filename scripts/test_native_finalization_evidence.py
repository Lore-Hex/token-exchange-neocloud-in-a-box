# SPDX-License-Identifier: Apache-2.0
"""Original repeat-build receipts, with no promotion to boot or confidentiality."""
import hashlib
import json
from pathlib import Path
import unittest

import compare_native_exports as compare
import mkosi_verity as verity
from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-native-root-finalization"
PRODUCER = "e0785af803f9e5752ca76bd3f6bf548203d7ba3f"
HASHES = {
    "attempt-1/manifest.json": "3fa94d1f34ca7187062161bd1a0c03ee26cf6e261e0fe5b5a0ef6fc617cb5c87",
    "attempt-1/resources.json": "69bc7b5d58d252dc3fe92bf7bcf98453dcc32e7d257e2707f1863fc2651be702",
    "attempt-1/result.json": "c78b1e903e31a13737457488986ff401c773fe866f731ed6f25c6652c9265b2a",
    "attempt-1/run.json": "39004e91a94d6c8a74d0bde80bb3d0cc287aadd734c9af7ed46f15296deb6437",
    "attempt-1/validation.json": "7b772704e0846a23fd3016954c1fdbcecd0fda5d2055537274de2b318159dff7",
    "attempt-2/manifest.json": "af582fb78d24eae31c9ead2498e0e815fee4535cb2e81aa6f263da1ed974c8bd",
    "attempt-2/resources.json": "afa389fdbf46aba4453d9a3f396ba2f5ffde76dae23c7574f1766c8df0de1c9b",
    "attempt-2/result.json": "fb63bf2d2bfa4a4b284e5cd734a44acca6d8d262ad6ce9db00d3b1dbc8059372",
    "attempt-2/run.json": "0e4860655eaeeec1c2e7bab214faf88127d90041b2a9d3256cf0b0edc9e1cf31",
    "attempt-2/validation.json": "7b772704e0846a23fd3016954c1fdbcecd0fda5d2055537274de2b318159dff7",
    "comparison.json": "14d6f179917a6aedf382261d5104feedb01390d9cef964e4272f654ebfd1b879",
}


def receipt(attempt, name):
    return json.loads((EVIDENCE / f"attempt-{attempt}" / name).read_bytes())


class NativeFinalizationEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_exact_producer_closure(self):
        for name, expected in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), expected)
        for attempt in (1, 2):
            directory = EVIDENCE / f"attempt-{attempt}"
            self.assertEqual({p.name for p in directory.iterdir()}, set(compare.NAMES) | {"output.log"})
            self.assertEqual((directory / "output.log").read_bytes(), (directory / "validation.json").read_bytes())
            self.assertEqual(receipt(attempt, "run.json")["git_sha"], PRODUCER)
            self.assertEqual(receipt(attempt, "run.json")["result"], "pass")
        first = receipt(1, "validation.json")
        self.assertEqual(first, receipt(2, "validation.json"))
        self.assertEqual(len(first["source"]["sources"]), 42)
        for name, expected in {**first["source"]["sources"], **first["validator_source"]}.items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), expected)

    def test_comparison_replays_and_qualifications_stay_false(self):
        raws = [{name: (EVIDENCE / f"attempt-{i}" / name).read_bytes() for name in compare.NAMES} for i in (1, 2)]
        report = compare.compare(*raws)
        self.assertEqual(compare.export.encoded(report), (EVIDENCE / "comparison.json").read_bytes())
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["matches"], {"root.raw": True, "nib.efi": True, "verity_root": True})
        for key in ("independent_builders_verified", "boot_verified", "signed", "confidentiality_verified"):
            self.assertIs(report[key], False)
        for i in (1, 2):
            compare.export.validate_resources(receipt(i, "resources.json"))
            validation = receipt(i, "validation.json")
            self.assertIs(validation["private_images_removed"], True)
            for key in compare.QUALIFICATIONS:
                self.assertIs(validation[key], False)

    def test_only_discarded_build_artifacts_differ_in_fixed_diagnostics(self):
        a, b = (receipt(i, "result.json")["worker"]["verity_root"] for i in (1, 2))
        self.assertEqual(a["source_tree_diagnostics"], b["source_tree_diagnostics"])
        self.assertEqual(set(a["discarded_build_artifacts"]), set(verity.BUILD_ARTIFACTS))
        self.assertEqual(set(b["discarded_build_artifacts"]), set(verity.BUILD_ARTIFACTS))
        for name in verity.BUILD_ARTIFACTS:
            left, right = (row["discarded_build_artifacts"][name] for row in (a, b))
            self.assertEqual(left["bytes"], right["bytes"])
            self.assertNotEqual(left["sha256"], right["sha256"])
        for row in (a, b):
            selected = row["source_tree_diagnostics"]["selected_files"]
            self.assertIsNone(selected[verity.BUILD_ARTIFACTS[0]])
            self.assertGreater(selected["etc/ld.so.cache"]["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
