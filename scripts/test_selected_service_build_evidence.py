# SPDX-License-Identifier: Apache-2.0
"""Pin original build artifacts without upgrading build success to serving."""

import copy
import unittest
from pathlib import Path

import selected_service_image as image


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-24-selected-service-image"
HASHES = {
    "preparation.json": "e920ff237959b92dcc2fbac9f4762135108e11c3efbfa362269381d9a9856740",
    "build.json": "903fb7a91f79f7e184674633235a573867dd279f4dfe20d33efe337d928d185b",
}


class BuildEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.raw = {name: (EVIDENCE / name).read_bytes() for name in HASHES}
        self.rows = {name: image.owner.service.correctness.strict_json(raw)
                     for name, raw in self.raw.items()}

    def validate(self, raw, expected):
        files = self.rows["preparation.json"]["files"]
        contract = {
            "base_image": "vllm/vllm-openai@sha256:" + files["metadata/manifest.json"]["sha256"],
            "official_config": {"sha256": files["metadata/config.json"]["sha256"]},
        }
        return image.validate_build_record(raw, expected, self.rows["preparation.json"],
                                          HASHES["preparation.json"], contract)

    def test_original_bytes_and_source_identity(self):
        for name, expected in HASHES.items():
            self.assertEqual(image.digest(self.raw[name]), expected)
        preparation = self.rows["preparation.json"]
        self.assertEqual(preparation["git_sha"], "93a6a86ce7d177d2be20db67f950f581e58c529e")
        self.assertEqual(preparation["files"]["context/payload/lab.json"]["sha256"],
                         "9e133c5a16accb8c222266344bd8dfdfdaa1cd841c65e90973f8569f920104df")

    def test_original_build_handoff(self):
        row = self.validate(self.raw["build.json"], HASHES["build.json"])
        self.assertEqual(row["image_id"],
                         "sha256:eb0c7a2a2ccf7f000d1678a3c4aba57e6918ef97a4817eb5a8e829c2768741b0")
        self.assertTrue(row["image_built"])
        self.assertTrue(row["build_completed"])
        for name in ("runtime_started", "hardware_validated", "deployable", "published"):
            self.assertIs(row[name], False)

    def test_receipt_cannot_claim_serving_or_publication(self):
        for name in ("runtime_started", "hardware_validated", "deployable", "published"):
            changed = copy.deepcopy(self.rows["build.json"])
            changed[name] = True
            raw = image.encoded(changed)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.validate(raw, image.digest(raw))

    def test_wrong_receipt_hash_fails(self):
        with self.assertRaises(ValueError):
            self.validate(self.raw["build.json"], "0" * 64)


if __name__ == "__main__":
    unittest.main()
