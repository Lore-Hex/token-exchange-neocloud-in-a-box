# SPDX-License-Identifier: Apache-2.0
"""A ready engine and clean teardown do not imply a measured stream cohort."""

import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-23-stream-preparation-failure"
PRODUCER = "dc65584ba6414ec0859d648ce0b610a293dd89a6"
HASHES = {
    "run.json": "f67871de70f1204632b12e828bc5cfc65750c34ff71ba3d840a6cc5969d5b01a",
    "output.log": "ad268278f9b8416fa65814a324af953d9f9f6cd1b8e610813adb6054c3bb1f52",
    "input-contract.json": "b1f24a0e06c0c19e988d0ccd0f9493a6196cee595433caea61038da7cc81ae7b",
    "receipt.json": "8f79421564325a968e8126cf9e6eed6357f29c75fbae2d5c56fa00706b2ad5e6",
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


class StreamFailureEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_source_bound_bundle(self):
        for name, expected in HASHES.items():
            self.assertEqual(hashlib.sha256((DIRECTORY / name).read_bytes()).hexdigest(), expected)
        run, report, contract, receipt = (document(name) for name in HASHES)
        for value in (run, report["provenance"], contract, receipt):
            self.assertEqual(value["git_sha"], PRODUCER)
        self.assertEqual(report["provenance"]["receipt_sha256"], HASHES["receipt.json"])
        self.assertEqual(receipt["files"]["payload/lab.json"], {
            "bytes": (DIRECTORY / "input-contract.json").stat().st_size,
            "sha256": HASHES["input-contract.json"],
        })
        for key in ("source_bundle_sha256", "manifest_sha256", "pack_revision"):
            self.assertEqual(report["provenance"][key], contract[key])
        self.assertEqual(report["provenance"]["payload_sha256"], receipt["payload_sha256"])
        self.assertEqual(len(receipt["files"]), 110)
        for name, sha in contract["sources"].items():
            self.assertEqual(receipt["files"]["payload/" + name]["sha256"], sha)
        source_map = {"record-deepseek-lab.py": "host/record-deepseek-lab.py",
                      "payload/stream-lab.py": "scripts/stream_lab.py",
                      "payload/tokenizer-parity.py": "scripts/engine_tokenizer_parity.py",
                      "payload/runtime-observation.py": "scripts/engine_runtime_observation.py"}
        source_map.update({name: "scripts/" + name.removeprefix("payload/stream/")
                           for name in receipt["files"] if name.startswith("payload/stream/")})
        for name, source in source_map.items():
            raw = git(ROOT, "show", PRODUCER + ":" + source)
            self.assertEqual(receipt["files"][name], {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated lab required.")\n'
        launch = prefix + b"\n".join(git(ROOT, "show", PRODUCER + ":scripts/" + source) for source in (
            "engine_python_bootstrap.py", "engine_exception.py", "engine_lab_bootstrap.py", "engine_lab.py"))
        self.assertEqual(receipt["files"]["payload/launch.py"],
                         {"bytes": len(launch), "sha256": hashlib.sha256(launch).hexdigest()})

    def test_ready_is_not_benchmark_success(self):
        run, report = document("run.json"), document("output.log")
        experiment = report["experiment"]
        self.assertEqual((run["started_utc"], run["ended_utc"]),
                         ("2026-10-23T01:33:20Z", "2026-10-23T01:59:50Z"))
        self.assertEqual(experiment["startup"]["outcome"], "ready")
        self.assertEqual(experiment["startup"]["wait_ms"], 1229399)
        self.assertEqual(experiment["stage"], "request")
        self.assertIsNone(experiment["tokenizer_parity"])
        self.assertIsNone(experiment["stream_cohort"])
        self.assertEqual(len(experiment["runtime_observation"]["samples"]), 1)
        self.assertEqual(experiment["runtime_observation"]["samples"][0]["event"], "before-requests")
        self.assertTrue(experiment["engine_stopped"])
        self.assertEqual(report["cleanup"], "confirmed")
        self.assertEqual(report["container_observation"], {"status": "exited", "exit_code": 0, "oom_killed": False})
        self.assertEqual(run["result"], "fail")
        self.assertEqual(report["result"], "fail")
        self.assertFalse(run["hardware_validated"])
        for value in (report, experiment):
            self.assertEqual(value["status"], "BLOCKED")
            for field in ("functional_pass", "deployable", "confidential"):
                self.assertIs(value[field], False)
        self.assertFalse(experiment["hardware"]["attestation_verified"])


if __name__ == "__main__":
    unittest.main()
