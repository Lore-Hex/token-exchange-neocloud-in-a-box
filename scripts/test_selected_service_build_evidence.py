# SPDX-License-Identifier: Apache-2.0
"""Pin original build artifacts without upgrading build success to serving."""

import copy
import unittest
from pathlib import Path

import selected_service_image as image


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2"
CASES = (
    {
        "directory": "2026-10-24-selected-service-image",
        "git_sha": "93a6a86ce7d177d2be20db67f950f581e58c529e",
        "lab_sha256": "9e133c5a16accb8c222266344bd8dfdfdaa1cd841c65e90973f8569f920104df",
        "image_id": "sha256:eb0c7a2a2ccf7f000d1678a3c4aba57e6918ef97a4817eb5a8e829c2768741b0",
        "hashes": {
            "preparation.json": "e920ff237959b92dcc2fbac9f4762135108e11c3efbfa362269381d9a9856740",
            "build.json": "903fb7a91f79f7e184674633235a573867dd279f4dfe20d33efe337d928d185b",
        },
    },
    {
        "directory": "2026-10-24-selected-service-rebuilds/638a3ad",
        "git_sha": "638a3ad1483910e894901f57363a4286b515e2dc",
        "lab_sha256": "d54480638c8a12989d426448b60099fa0bbb4b02fb1f046e3098d4da87e17c2e",
        "image_id": "sha256:2857237c7ad23b66bcb73f5943d51721b37ed5c60542f4d5eabab51c49df1119",
        "hashes": {
            "preparation.json": "2a714f0cd04a9242e0f03bf8e55dfbd0f5c6fa86b97762ffb54c2fd1d8d4eca7",
            "build.json": "67b963a0f6bf35c294a265799db39bc958db16867a468569eea96a6c95310410",
        },
    },
    {
        "directory": "2026-10-24-selected-service-rebuilds/ab759d",
        "git_sha": "ab759d43b82e5e235ddb99eb5e5d5ebcfb360e63",
        "lab_sha256": "6ce90d0d7b31a5a6325df037688579e9fb40e75ac8c857b3c1538a09896de878",
        "image_id": "sha256:e1b4bd3d5713a412c8ca1e55aa537bb679fa29ca1a88e663e934b809dbbff49b",
        "hashes": {
            "preparation.json": "08e7d4b45a7041f53a866d67a359b20350578f63d876af3d64027ca2d71341bf",
            "build.json": "68f9f233858f568469a8c7f4f8f8daa14deac7b525f6a5583032ff11df36df96",
        },
    },
)


class BuildEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        for case in CASES:
            raw = {name: (EVIDENCE / case["directory"] / name).read_bytes()
                   for name in case["hashes"]}
            rows = {name: image.owner.service.correctness.strict_json(data)
                    for name, data in raw.items()}
            self.records.append((case, raw, rows))

    def validate(self, raw, expected, preparation, preparation_sha):
        files = preparation["files"]
        contract = {
            "base_image": "vllm/vllm-openai@sha256:" + files["metadata/manifest.json"]["sha256"],
            "official_config": {"sha256": files["metadata/config.json"]["sha256"]},
        }
        return image.validate_build_record(raw, expected, preparation, preparation_sha, contract)

    def test_original_bytes_and_source_identity(self):
        for case, raw, rows in self.records:
            with self.subTest(source=case["git_sha"]):
                for name, expected in case["hashes"].items():
                    self.assertEqual(image.digest(raw[name]), expected)
                preparation = rows["preparation.json"]
                self.assertEqual(preparation["git_sha"], case["git_sha"])
                self.assertEqual(preparation["files"]["context/payload/lab.json"]["sha256"],
                                 case["lab_sha256"])
                for name in ("image_built", "hardware_validated", "deployable"):
                    self.assertIs(preparation[name], False)

    def test_original_build_handoff(self):
        for case, raw, rows in self.records:
            with self.subTest(source=case["git_sha"]):
                row = self.validate(raw["build.json"], case["hashes"]["build.json"],
                                    rows["preparation.json"], case["hashes"]["preparation.json"])
                self.assertEqual(row["image_id"], case["image_id"])
                self.assertTrue(row["image_built"])
                self.assertTrue(row["build_completed"])
                for name in ("runtime_started", "hardware_validated", "deployable", "published"):
                    self.assertIs(row[name], False)

    def test_receipt_cannot_claim_serving_or_publication(self):
        for case, _, rows in self.records:
            for name in ("runtime_started", "hardware_validated", "deployable", "published"):
                changed = copy.deepcopy(rows["build.json"])
                changed[name] = True
                raw = image.encoded(changed)
                with self.subTest(source=case["git_sha"], name=name), self.assertRaises(ValueError):
                    self.validate(raw, image.digest(raw), rows["preparation.json"],
                                  case["hashes"]["preparation.json"])

    def test_wrong_receipt_hash_fails(self):
        for case, raw, rows in self.records:
            with self.subTest(source=case["git_sha"]), self.assertRaises(ValueError):
                self.validate(raw["build.json"], "0" * 64, rows["preparation.json"],
                              case["hashes"]["preparation.json"])

    def test_different_preparations_cannot_be_substituted(self):
        for case, raw, _ in self.records:
            for other, _, rows in self.records:
                if case == other:
                    continue
                with self.subTest(source=case["git_sha"], other=other["git_sha"]), \
                        self.assertRaises(ValueError):
                    self.validate(raw["build.json"], case["hashes"]["build.json"],
                                  rows["preparation.json"], other["hashes"]["preparation.json"])


if __name__ == "__main__":
    unittest.main()
