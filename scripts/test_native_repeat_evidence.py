# SPDX-License-Identifier: Apache-2.0
"""Retained original repeat-workflow receipts, without added qualifications."""
import hashlib
import json
from pathlib import Path
import unittest

import compare_native_exports as compare
from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-native-repeat-workflow"
PRODUCER = "72aad43b1e3cb77c203d2620d7fca9c2e577dcf3"
HASHES = {
    "first/manifest.json": "4911421ee9f63cf3d24b455bd43118b3d4a33e58e28db1310d919312279d3b4a",
    "first/resources.json": "93b8ae04131b1fa618f01714c5a4aa8ce4c511b77ad7aae02b15ca0f5ff196c8",
    "first/result.json": "302a9cd9f4af6bf8a19f7b34bc4bac8bbfa878f1af79edd1587c1cc768da9133",
    "first/run.json": "a21feafaad6ac5e6a8ff02d0c7162373d8b7f789b8fac64f4147c27f2a004cf6",
    "first/validation.json": "c2b053836de11c9fc2d297e3c51445811de0c104af7d201d400eb499fe6d00c6",
    "second/manifest.json": "de2b524dec05db80ece46b1b73bf51df0dcb85f485ff050bc7084469c606480b",
    "second/resources.json": "50f7185131cb0e47015525a6e58eba752daa0701dfc2370e9ee959e12accb7ae",
    "second/result.json": "54c5f11680975ac1f5e04f574a2d6609dd1cae8cb50f6c9ed20b434d94d12655",
    "second/run.json": "36726cc63d2adc0d95961662840d5167b91d7bef176cc895f8d3d66817a93c2d",
    "second/validation.json": "c2b053836de11c9fc2d297e3c51445811de0c104af7d201d400eb499fe6d00c6",
    "comparison.json": "0ae7492f41d4871f84d2c187d4f8396557788a67bffe8c5532baa293e5994938",
    "workflow.json": "2c17b43f0d015f20bb7881a7739e6c465c73696e0ec9668c5582c8d39e4b5f0c",
}


class NativeRepeatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.raws = [{name: (EVIDENCE / slot / name).read_bytes() for name in compare.NAMES}
                     for slot in ("first", "second")]

    def test_original_bytes_and_actual_producer_source_closure(self):
        for path, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / path).read_bytes()).hexdigest(), digest)
        for slot, raws in zip(("first", "second"), self.raws):
            directory = EVIDENCE / slot
            self.assertEqual({path.name for path in directory.iterdir()}, set(compare.NAMES) | {"output.log"})
            self.assertEqual((directory / "output.log").read_bytes(), raws["validation.json"])
            validated = compare.validate(raws)
            receipt = validated["validation.json"]
            self.assertEqual(receipt["source"]["git_sha"], PRODUCER)
            self.assertEqual(len(receipt["source"]["sources"]), 42)
            for path, digest in {**receipt["source"]["sources"], **receipt["validator_source"]}.items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + path)).hexdigest(), digest)

    def test_recorded_jobs_are_distinct_sequential_and_contain_their_builds(self):
        workflow = json.loads((EVIDENCE / "workflow.json").read_bytes())
        self.assertEqual((workflow["databaseId"], workflow["attempt"], workflow["headSha"], workflow["conclusion"]),
                         (39851595140, 1, PRODUCER, "success"))
        jobs = workflow["jobs"]
        self.assertEqual([job["name"] for job in jobs], ["first / install", "second / install", "compare"])
        self.assertEqual(len({job["databaseId"] for job in jobs}), 3)
        for index, job in enumerate(jobs):
            self.assertEqual((job["status"], job["conclusion"]), ("completed", "success"))
            self.assertLess(compare.timestamp(job["startedAt"]), compare.timestamp(job["completedAt"]))
            if index:
                self.assertLessEqual(compare.timestamp(jobs[index - 1]["completedAt"]),
                                     compare.timestamp(job["startedAt"]))
            if index < 2:
                run = json.loads(self.raws[index]["run.json"])
                self.assertLessEqual(compare.timestamp(job["startedAt"]), compare.timestamp(run["started_utc"]))
                self.assertLessEqual(compare.timestamp(run["ended_utc"]), compare.timestamp(job["completedAt"]))

    def test_original_comparison_replays_without_claiming_other_qualifications(self):
        report = compare.compare(*self.raws, expected_source=PRODUCER)
        self.assertEqual(compare.export.encoded(report), (EVIDENCE / "comparison.json").read_bytes())
        self.assertEqual(report["result"], "pass")
        self.assertEqual(report["matches"], {"root.raw": True, "nib.efi": True, "verity_root": True})
        for key in ("independent_builders_verified", "boot_verified", "signed", "confidentiality_verified"):
            self.assertIs(report[key], False)


if __name__ == "__main__":
    unittest.main()
