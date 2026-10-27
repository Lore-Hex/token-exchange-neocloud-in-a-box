# SPDX-License-Identifier: Apache-2.0
"""Original timeout evidence and exact source recovery; no network or boot."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-27-debug-kernel-fetch-timeout"
PRODUCER = "0a77a4d834a7ed18f1a3510f2714ee9276955b00"
ARCHIVE_SHA = "b3f16d16b54796497884b27810b0942243bdc6ef26da1ce821e948ece39a9492"
ORIGINALS = {
    "validation.json": (8261, "d2fb42bae5dac8c09bdc92d9dbf5de591020202370c8f70d5617f50afbc439fe"),
    "github-run.json": (3081, "dae2dbb3159165444d17e55e1c04649b521cc98a5bcd962679dcb81ec5f017f5"),
    "github-artifacts.json": (773, "090b0f05080e82be52237e8228c080fd3db5278f67f16a6c6e6ac68d71a08abc"),
    "failed-workflow.log": (4549, "326efadad392d55488cb91a8692c48846e68f9eef2c6d3dbd5be56934cd040e6"),
    "primary-transfer.json": (168, "0f3b6b3dbc339e3a465718fb20c08a6052b8f5e139eaddf137942e828e870e77"),
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class KernelFetchEvidenceTests(unittest.TestCase):
    def test_original_bytes_preserved_without_synthetic_run_receipt(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(ORIGINALS) | {"README.md"})
        for name, (size, digest) in ORIGINALS.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest))

    def test_original_cleanup_is_source_bound_but_not_build_or_boot(self):
        record = receipt("validation.json")
        self.assertEqual(record["schema"], "nib.native-private-export-validation/v1")
        self.assertEqual((record["result"], record["build_result"]), ("fail", "skipped"))
        self.assertEqual(record["failure"], {"schema": "nib.native-build-failure/v1",
                         "git_sha": PRODUCER, "installation": None, "resources": None})
        for name in ("artifacts_verified", "boot_verified", "confidentiality_verified", "reproducibility_verified", "signed"):
            self.assertIs(record[name], False)
        self.assertIs(record["private_images_removed"], True)
        self.assertIs(record["metadata_only"], True)
        self.assertEqual((record["source"]["git_sha"], record["source"]["profile"]), (PRODUCER, "guest-network/v1"))
        self.assertEqual(len(record["source"]["sources"]), 68)
        self.assertEqual(len(record["validator_source"]), 2)
        for sources in (record["source"]["sources"], record["validator_source"]):
            for name, digest in sources.items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)

    def test_terminal_github_state_and_only_cleanup_artifact(self):
        run, artifacts = receipt("github-run.json"), receipt("github-artifacts.json")
        self.assertEqual((run["databaseId"], run["attempt"], run["headSha"], run["event"], run["status"], run["conclusion"]),
                         (40516934761, 1, PRODUCER, "workflow_dispatch", "completed", "failure"))
        job = run["jobs"][0]
        self.assertEqual((job["startedAt"], job["completedAt"], job["status"]),
                         ("2026-10-27T13:59:29Z", "2026-10-27T14:04:11Z", "completed"))
        steps = {row["number"]: row for row in job["steps"]}
        self.assertEqual([steps[n]["conclusion"] for n in (2, 5, 6, 7, 8, 9, 10)],
                         ["success", "failure", "skipped", "skipped", "success", "skipped", "success"])
        self.assertEqual((artifacts["total_count"], len(artifacts["artifacts"])), (1, 1))
        artifact = artifacts["artifacts"][0]
        self.assertEqual((artifact["id"], artifact["name"], artifact["size_in_bytes"]),
                         (12740237199, "unused-debug-export-40516934761-1", 3761))
        self.assertEqual(artifact["digest"], "sha256:308df18c1ee462bebd5284d33b78896a5362a25538cb22284e910c09e84e92f3")
        self.assertFalse(artifact["expired"])
        self.assertEqual((artifact["workflow_run"]["id"], artifact["workflow_run"]["head_sha"]), (40516934761, PRODUCER))

    def test_typed_timeouts_map_to_exact_locked_package_not_http_or_checksum_failure(self):
        raw = (EVIDENCE / "failed-workflow.log").read_text()
        self.assertIn("build/native-install/metadata.tar.gz: OK", raw)
        rows = [json.loads(line[line.index('{"'):]) for line in raw.splitlines() if '"schema": "nib.input-fetch-' in line]
        expected = {"expected_bytes": 113500352, "expected_sha256": ARCHIVE_SHA,
                    "failure": "timeout", "input_kind": "archive", "package_execution": False, "returncode": None}
        self.assertEqual(rows, [
            {**expected, "schema": "nib.input-fetch-retry/v1", "result": "retry", "failed_attempt": 1, "delay_seconds": 1},
            {**expected, "schema": "nib.input-fetch-retry/v1", "result": "retry", "failed_attempt": 2, "delay_seconds": 2},
            {**expected, "schema": "nib.input-fetch-failure/v1", "result": "fail"},
        ])
        for path in ("image/inputs/guest-runtime-ubuntu2404.json", "image/inputs/r595-ubuntu2404.json"):
            lock = json.loads(git(ROOT, "show", PRODUCER + ":" + path))
            packages = [row for row in lock["packages"] if row["SHA256"] == ARCHIVE_SHA]
            self.assertEqual(len(packages), 1)
            row = packages[0]
            self.assertEqual((row["Package"], row["Version"], row["Size"], row["index"]),
                             ("linux-modules-extra-6.8.0-147-generic", "6.8.0-147.147", "113500352", "noble-updates-main"))
            index = next(i for i in lock["indexes"] if i["id"] == row["index"])
            self.assertEqual(index["base_url"], "https://archive.ubuntu.com/ubuntu/")

    def test_local_primary_transfer_is_only_a_bounded_download_observation(self):
        self.assertEqual(receipt("primary-transfer.json"), {
            "source": "locked-primary", "http_status": 200, "elapsed_seconds": 9.603884,
            "received_bytes": 113500352, "start_transfer_seconds": 0.522292,
            "speed_bytes_per_second": 11818171,
        })


if __name__ == "__main__":
    unittest.main()
