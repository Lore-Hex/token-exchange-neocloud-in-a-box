# SPDX-License-Identifier: Apache-2.0
"""Retain successful individual builds and the failed cross-build comparison."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import compare_native_exports as compare

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-native-repro-mismatch"
PRODUCER = "c8853e1ea7f537d7a7888d79e41d669032f8f19f"
HASHES = {
    "attempt-1/manifest.json": "e3e5d214e5b05436c8d09b7785c67ccaa476b5959ae21c8894c9ea15bfc3d032",
    "attempt-1/resources.json": "0a1db9727a6bcc4ff27bb9a5c10372b8be7d88f747db47a528b60938483dcaf9",
    "attempt-1/result.json": "a66918d8cf64d783bbed013a0b8e55b108c22e86d59cbe35133ba54196d0d530",
    "attempt-1/run.json": "37f39bdfa2802dce05484d77d7dd366fba6c2e37cd3cfa1ae83973d9bd1a2e8f",
    "attempt-1/validation.json": "c2cf2b5b96a3cea7802ee435c6cea111f193e99384e9621681663db833f05dc4",
    "attempt-2/manifest.json": "9605d5d9459a6986d7ca00a8120815e6e430a67eb6ea24c07c97f601731bd1f3",
    "attempt-2/resources.json": "046be064916b9e9c092b42b71e8623b27d93e1e18082cf73342ec888c8da17b4",
    "attempt-2/result.json": "9e639ca50fa26e41e69e38a83d6f8ed0bf53c42aa3a1aa451305c0970f4bbbab",
    "attempt-2/run.json": "0e227bc8d3ff401b7101dfe96d726ec77226dd666fe64cec1a82f0f23232b446",
    "attempt-2/validation.json": "705cc9509d39b9d5770a9d998bee38d2a996d706061c957e1c148e433dfee9fc",
    "comparison.json": "d5543b8d22110f21e8f47cf9d5b76fb1b7fc955aecc69e1be5eda11df8612ef6",
}


def receipt(attempt, name):
    return json.loads((EVIDENCE / f"attempt-{attempt}" / name).read_bytes())


class NativeReproEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_source_closure(self):
        for name, expected in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), expected)
        for attempt in (1, 2):
            directory = EVIDENCE / f"attempt-{attempt}"
            self.assertEqual({p.name for p in directory.iterdir()}, set(compare.NAMES) | {"output.log"})
            self.assertEqual((directory / "output.log").read_bytes(), (directory / "validation.json").read_bytes())
            self.assertEqual(receipt(attempt, "run.json")["git_sha"], PRODUCER)
            self.assertEqual(receipt(attempt, "run.json")["result"], "pass")
        first = receipt(1, "validation.json")
        self.assertEqual(first["source"], receipt(2, "validation.json")["source"])
        self.assertEqual(len(first["source"]["sources"]), 42)
        for name, expected in {**first["source"]["sources"], **first["validator_source"]}.items():
            raw = subprocess.check_output(["git", "show", f"{PRODUCER}:{name}"], cwd=ROOT, timeout=10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

    def test_failed_comparison_replays_without_relabeling_build_success(self):
        raws = [{name: (EVIDENCE / f"attempt-{i}" / name).read_bytes() for name in compare.NAMES} for i in (1, 2)]
        report = compare.compare(*raws)
        self.assertEqual(compare.export.encoded(report), (EVIDENCE / "comparison.json").read_bytes())
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["matches"], {"root.raw": False, "nib.efi": False, "verity_root": False})
        for key in ("independent_builders_verified", "boot_verified", "signed", "confidentiality_verified"):
            self.assertIs(report[key], False)

    def test_equal_components_do_not_hide_root_mismatch(self):
        a, b = (receipt(i, "result.json")["worker"] for i in (1, 2))
        self.assertEqual(a["driver_build"], b["driver_build"])
        self.assertEqual(a["boot_uki"]["initrd"], b["boot_uki"]["initrd"])
        for name in (".linux", ".initrd", ".osrel", ".uname"):
            self.assertEqual(a["boot_uki"]["uki"]["sections"][name], b["boot_uki"]["uki"]["sections"][name])
        self.assertNotEqual(a["boot_uki"]["uki"]["sections"][".cmdline"], b["boot_uki"]["uki"]["sections"][".cmdline"])
        self.assertNotEqual(a["verity_root"]["root_hash"], b["verity_root"]["root_hash"])
        for i in (1, 2):
            resources = receipt(i, "resources.json")
            compare.export.validate_resources(resources)
            self.assertTrue(receipt(i, "validation.json")["private_images_removed"])


if __name__ == "__main__":
    unittest.main()
