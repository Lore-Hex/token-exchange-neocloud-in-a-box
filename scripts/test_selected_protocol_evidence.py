# SPDX-License-Identifier: Apache-2.0
"""Immutable historical observation, not a reappraisal under a newer contract."""

from pathlib import Path
import unittest

import selected_service_image as image
import selected_service_conformance as subset


DIRECTORY = Path(__file__).resolve().parents[1] / "evidence/p2/2026-10-25-selected-protocol-drain"
HASHES = {
    "smoke.json": "a54d1b056d3fff4811448d52c002a20b5b4e722214120c10bece55f79f3e88b7",
    "build.json": "3999ada0708cf27c34404b96475eb821e9dc201aa4f49062e0cb26467e0ae09d",
    "preparation.json": "6be2291143c7b86837720efd0490842e3d6b56c59e526180530ecf0212204b84",
}


class OriginalProtocolEvidenceTests(unittest.TestCase):
    def test_original_failure_preserves_protocol_pass_and_build_identity(self):
        raw = {name: (DIRECTORY / name).read_bytes() for name in HASHES}
        for name, expected in HASHES.items():
            self.assertEqual(image.digest(raw[name]), expected)
        self.assertEqual(len(raw["smoke.json"]), 5357)
        rows = {name: image.owner.service.correctness.strict_json(value) for name, value in raw.items()}
        prep, row = rows["preparation.json"], rows["smoke.json"]
        self.assertEqual(prep["git_sha"], "676c2a7b7344942f9d0e841518cb1f825854afc2")
        self.assertEqual(len(prep["files"]), 793)
        build = image.validate_build_record(raw["build.json"], HASHES["build.json"], prep,
            HASHES["preparation.json"], {
                "base_image": "vllm/vllm-openai@sha256:" + prep["files"]["metadata/manifest.json"]["sha256"],
                "official_config": {"sha256": prep["files"]["metadata/config.json"]["sha256"]}})
        for field in ("image_id", "prepared_sha256", "preparation_sha256"):
            self.assertEqual(row[field], build[field])
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        self.assertEqual((row["result"], row["status"], row["cleanup"]), ("fail", "BLOCKED", "confirmed"))
        self.assertIsNone(row["runtime_started"])
        owner = row["owner_failure"]
        protocol = owner["protocol"]
        self.assertEqual(protocol["contract_sha256"],
            "d18de61288cb8bb872a4dade440b71f8bce4ae8cc58fcc3ecc1467d606e6d6e1")
        self.assertEqual(protocol["checks"], dict.fromkeys(subset.pinned.MANDATORY, "pass"))
        self.assertEqual((protocol["result"], protocol["failure"], protocol["checker_result"]),
                         ("fail", "completion", "pass"))
        self.assertTrue(protocol["all_17_pass"])
        self.assertEqual(protocol["usage"]["drain"], {"prompt_tokens": 51, "completion_tokens": 256})
        self.assertFalse(any(protocol["usage_unknown"].values()))
        self.assertEqual(protocol["drain"], {"completed": False, "first_content": False,
            "reader_joined": False, "reader_started": False, "resources_closed": True,
            "retirement_requested": False, "signal_sent": False})
        self.assertEqual(owner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                          for role in ("tls", "guard", "engine")])
        self.assertTrue(owner["cleanup_confirmed"])
        for scope in (row, owner, protocol, protocol["subset"], owner["inference"]):
            for name in ("confidential", "e2ee", "zdr", "deployable", "hardware_validated", "tools", "reasoning"):
                self.assertIs(scope[name], False)


if __name__ == "__main__":
    unittest.main()
