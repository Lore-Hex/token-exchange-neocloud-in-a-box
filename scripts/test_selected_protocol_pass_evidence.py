# SPDX-License-Identifier: Apache-2.0
"""Preserve the original private protocol success without promoting its scope."""

from pathlib import Path
import subprocess
import unittest

import selected_service_image as image
import selected_service_protocol as protocol


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-protocol-pass"
REVISION = "faf23b0babf55eb5383da3c3d28ce11443716d3b"
HASHES = {
    "smoke.json": "5c1b3f01d8f914963eeb24a69479ef9bc8ab00a7fbeb51411020c6e173d962fc",
    "build.json": "5ab7a1412e3c1226dcc3efe99e0c2885384e2aedc8f45f313db7ac8e8de2d826",
    "preparation.json": "831b0c86d85ed49c95ff442b01c1f7727cc9f800cadd66fc41bf02484d5f5821",
}


class ProtocolPassEvidenceTests(unittest.TestCase):
    def test_original_receipts_bind_source_image_checks_and_active_retirement(self):
        raw = {name: (DIRECTORY / name).read_bytes() for name in HASHES}
        for name, expected in HASHES.items():
            self.assertEqual(image.digest(raw[name]), expected)
        rows = {name: image.owner.service.correctness.strict_json(value) for name, value in raw.items()}
        prep, row = rows["preparation.json"], rows["smoke.json"]
        self.assertEqual(prep["git_sha"], REVISION)
        self.assertEqual(len(prep["files"]), 801)
        for name in ("selected_service.py", "selected_service_process.py", "selected_service_protocol.py",
                     "selected_service_conformance.py", "selected_service_inference.py"):
            source = subprocess.check_output(["git", "show", REVISION + ":scripts/" + name], cwd=ROOT)
            self.assertEqual(image.digest(source), prep["files"]["context/service/scripts/" + name]["sha256"])
        build = image.validate_build_record(raw["build.json"], HASHES["build.json"], prep,
            HASHES["preparation.json"], {
                "base_image": "vllm/vllm-openai@sha256:" + prep["files"]["metadata/manifest.json"]["sha256"],
                "official_config": {"sha256": prep["files"]["metadata/config.json"]["sha256"]}})
        for name in ("image_id", "prepared_sha256", "preparation_sha256"):
            self.assertEqual(row[name], build[name])
        self.assertEqual(row["build_sha256"], HASHES["build.json"])
        self.assertEqual((row["result"], row["status"], row["cleanup"]), ("pass", "PASS", "confirmed"))
        self.assertTrue(row["runtime_started"])
        owner = row["lifecycle"]
        observed = protocol.validate_report(protocol.encoded(owner["protocol"]), row["prepared_sha256"],
                                            owner["inference"])
        self.assertEqual(observed["checks"], dict.fromkeys(protocol.subset.pinned.MANDATORY, "pass"))
        self.assertEqual((observed["result"], observed["failure"], observed["checker_result"]),
                         ("pass", "none", "pass"))
        self.assertTrue(observed["all_17_pass"])
        self.assertEqual(observed["usage"]["drain"], {"prompt_tokens": 25, "completion_tokens": 256})
        self.assertFalse(any(observed["usage_unknown"].values()))
        self.assertTrue(all(observed["drain"].values()))
        self.assertEqual((owner["status"], owner["reason"], owner["ready"], owner["cleanup_confirmed"]),
                         ("retired", "retired", True, True))
        self.assertEqual(owner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                          for role in ("tls", "guard", "engine")])
        for scope in (row, owner, observed, observed["subset"], owner["inference"]):
            for name in ("confidential", "e2ee", "zdr", "deployable", "hardware_validated", "tools", "reasoning"):
                self.assertIs(scope[name], False)


if __name__ == "__main__":
    unittest.main()
