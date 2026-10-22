# SPDX-License-Identifier: Apache-2.0
"""Original real-checker negative control is not actual DeepSeek conformance."""

import hashlib
import json
from pathlib import Path
import subprocess
import unittest

import provider_conformance_worker as worker

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-22-provider-check-closed-endpoint"
PRODUCER = "720bfa7aabd787744a56a94a31162498cf235270"


class ProviderEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_committed_producer(self):
        for name, sha in (("run.json", "eed18cceae475c75f2b4113552f74993dcd30de45b7167a580641c5d212d291a"),
                          ("output.log", "4f02fc76d1ffa89a4de7517a6a068b2687a8aeda212895e5600737d1717cd267")):
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), sha)
        envelope = json.loads((EVIDENCE / "run.json").read_bytes())
        record = json.loads((EVIDENCE / "output.log").read_bytes())
        self.assertEqual(envelope["git_sha"], PRODUCER)
        self.assertEqual(record["producer"]["git_sha"], PRODUCER)
        for name, sha in record["producer"]["files"].items():
            raw = subprocess.check_output(["git", "show", f"{PRODUCER}:{name}"], cwd=ROOT, timeout=10)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)

    def test_upstream_warning_gate_is_not_accepted(self):
        envelope = json.loads((EVIDENCE / "run.json").read_bytes())
        record = json.loads((EVIDENCE / "output.log").read_bytes())
        report = record["report"]
        projection = report["projection"]
        self.assertEqual(report, worker.result("completed", projection))
        self.assertEqual(set(projection["checks"]), set(worker.ALLOWED))
        self.assertEqual(list(projection["checks"].values()).count("warn"), 7)
        self.assertEqual(list(projection["checks"].values()).count("skip"), 11)
        self.assertTrue(projection["upstream_conformance_gate"])
        self.assertFalse(projection["mandatory_checks_passed"])
        self.assertFalse(projection["native_identity_matches"])
        self.assertEqual(record["worker_exit_code"], 1)
        self.assertTrue(record["cleanup_confirmed"])
        self.assertFalse(record["deepseek_executed"])
        self.assertEqual(record["kind"], "local-linux-closed-endpoint-negative-control")
        self.assertEqual(record["payload_sha256"], "ba3768f58f82afd3b034998b6284470fab0ce8267b0ea8a1c94647f48deca446")
        self.assertEqual(record["selected_image"]["platform"], {"architecture": "amd64", "os": "linux"})
        self.assertEqual(envelope["execution"], "software")
        self.assertFalse(envelope["hardware_validated"])
        self.assertEqual(envelope["host"]["gpus"], "none")
        self.assertEqual(envelope["result"], "pass")


if __name__ == "__main__":
    unittest.main()
