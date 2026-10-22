# SPDX-License-Identifier: Apache-2.0
"""Original CPU-only boot failure: initrd observed, root proof absent."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-boot-root-failure"
PRODUCER = "c0900f178072a08b5cc8688638993ccfc3061ab7"
HASHES = {
    "resources.json": "4f4601a09d6b8167bfc6515d3c2bf22335f459ca95eb0c97b2814b3a67e5a9c7",
    "run.json": "2f74cdae1acb24489719ff96ea544ef49206e19501489ae410e4cf40e69c6185",
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "worker.json": "201eb046a2cc4a9c9899552ad318cd2935d268251f3659d4936dee3dece0d1f4",
}
READ_EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-boot-root-read-failure"
READ_PRODUCER = "28f9fc01d59bc316ffdb206c8c0434cd5ad191e7"
READ_HASHES = {
    "resources.json": "0311b338b6e36cc82b8dc73a4890b329cfc23a5972e518c9da303b7254e67828",
    "run.json": "48749e6fe0decca20152c40858e545c0f541fb856adaca2932d4eed406d09a69",
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "worker.json": "316631a11cde36f13e0aa64b9b49fd4903d3012c2792ac0856e66652a6a11513",
}
DEADLINE_EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-boot-read-deadline"
DEADLINE_PRODUCER = "6a15eee38c82225bc6649218829532d5ed135d63"
DEADLINE_HASHES = {
    "resources.json": "79c693cd69881c42c85f67a69e172cafd28ccb9cef1bedf9038078d13f580ab4",
    "run.json": "4cfb1c10a347b6b11b879a212ba9304d0a5d9ac3b9f548729ace1187f729c293",
    "validation.json": "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426",
    "worker.json": "24eb7213e29613be604781d84e398eab90ac307f68025fe0d57905969047b524",
}


def receipt(name, directory=EVIDENCE):
    return json.loads((directory / name).read_bytes())


class DebugBootEvidenceTests(unittest.TestCase):
    def test_original_four_json_files_are_byte_preserved(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(HASHES) | {"README.md", "output.log"})
        for name, digest in HASHES.items():
            self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        self.assertEqual(receipt("run.json")["result"], "fail")
        self.assertEqual(receipt("run.json")["started_utc"], "2026-10-22T19:55:00Z")
        self.assertEqual(receipt("run.json")["ended_utc"], "2026-10-22T19:56:34Z")

    def test_failed_root_proof_cannot_be_promoted_from_clean_exit_or_assembly(self):
        worker = receipt("worker.json")
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(len(worker["qemu"]["startup_checks"]), 5)
        self.assertTrue(all(item["result"] == "pass" for item in worker["qemu"]["startup_checks"]))
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
            "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        # The old receiver discarded failure codes: never retrofit a diagnosis.
        self.assertNotIn("guest_failure", worker["qemu"]["proof"])

    def test_independent_cleanup_and_exact_source_binding(self):
        state, worker = receipt("resources.json"), receipt("worker.json")
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual(state["oom_kill"], 0)
        self.assertEqual(state["child_exit_code"], 1)
        self.assertEqual(state["memory_peak_bytes"], 2147483648)
        self.assertTrue(receipt("validation.json")["private_images_removed"])
        for label in ("source", "debug_source"):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            for name, digest in worker[label]["sources"].items():
                raw = git(ROOT, "show", PRODUCER + ":" + name)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)


class RootReadFailureEvidenceTests(unittest.TestCase):
    def test_second_failure_originals_are_byte_preserved(self):
        self.assertEqual({p.name for p in READ_EVIDENCE.iterdir()}, set(READ_HASHES) | {"README.md", "output.log"})
        for name, digest in READ_HASHES.items():
            self.assertEqual(hashlib.sha256((READ_EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((READ_EVIDENCE / "output.log").read_bytes(), (READ_EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((READ_EVIDENCE / "run.json").read_bytes()), READ_PRODUCER)
        run = receipt("run.json", READ_EVIDENCE)
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-22T20:47:45Z", "2026-10-22T20:49:17Z"))

    def test_root_read_class_does_not_invent_a_deadline_or_boot_pass(self):
        worker = receipt("worker.json", READ_EVIDENCE)
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(len(worker["qemu"]["startup_checks"]), 5)
        self.assertTrue(all(item["result"] == "pass" for item in worker["qemu"]["startup_checks"]))
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False,
                         "guest_failure": "ROOT_READ", "protocol_error": None})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
                         "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json", READ_EVIDENCE)):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        self.assertNotIn("root_read", worker["qemu"]["proof"])

    def test_second_failure_cleanup_and_exact_producer_closure(self):
        state = receipt("resources.json", READ_EVIDENCE)
        worker = receipt("worker.json", READ_EVIDENCE)
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual((state["child_exit_code"], state["oom_kill"], state["memory_peak_bytes"]), (1, 0, 2147483648))
        self.assertTrue(receipt("validation.json", READ_EVIDENCE)["private_images_removed"])
        self.assertFalse(worker["private_work_removed"])
        for label in ("source", "debug_source"):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], READ_PRODUCER)
            for name, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", READ_PRODUCER + ":" + name)).hexdigest(), digest)


class RootReadDeadlineEvidenceTests(unittest.TestCase):
    def test_deadline_failure_originals_are_byte_preserved(self):
        self.assertEqual({p.name for p in DEADLINE_EVIDENCE.iterdir()},
                         set(DEADLINE_HASHES) | {"README.md", "output.log"})
        for name, digest in DEADLINE_HASHES.items():
            self.assertEqual(hashlib.sha256((DEADLINE_EVIDENCE / name).read_bytes()).hexdigest(), digest)
        self.assertEqual((DEADLINE_EVIDENCE / "output.log").read_bytes(),
                         (DEADLINE_EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((DEADLINE_EVIDENCE / "run.json").read_bytes()), DEADLINE_PRODUCER)
        run = receipt("run.json", DEADLINE_EVIDENCE)
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-22T21:29:41Z", "2026-10-22T21:31:43Z"))

    def test_deadline_classification_is_not_a_complete_root_proof(self):
        worker = receipt("worker.json", DEADLINE_EVIDENCE)
        self.assertEqual(worker["assembly"]["result"], "pass")
        self.assertEqual(len(worker["qemu"]["startup_checks"]), 5)
        self.assertTrue(all(item["result"] == "pass" for item in worker["qemu"]["startup_checks"]))
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False,
            "guest_failure": "ROOT_READ", "protocol_error": None,
            "root_read": {"reason": "deadline", "bytes_read": 171704320, "elapsed_seconds": 23}})
        self.assertEqual(worker["input_files"]["root.raw"]["bytes"], 928968704)
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
            "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, worker["qemu"], receipt("validation.json", DEADLINE_EVIDENCE)):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])

    def test_deadline_failure_cleanup_and_exact_source_closure(self):
        state = receipt("resources.json", DEADLINE_EVIDENCE)
        worker = receipt("worker.json", DEADLINE_EVIDENCE)
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual((state["child_exit_code"], state["oom_kill"]), (1, 0))
        self.assertTrue(receipt("validation.json", DEADLINE_EVIDENCE)["private_images_removed"])
        self.assertFalse(worker["private_work_removed"])
        for label in ("source", "debug_source"):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], DEADLINE_PRODUCER)
            for name, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", DEADLINE_PRODUCER + ":" + name)).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
