# SPDX-License-Identifier: Apache-2.0
"""Byte-preserved CPU-only debug-root success, with independent cleanup."""
import hashlib
import json
from pathlib import Path
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-22-debug-root-boot-pass"
PRODUCER = "eed4b10ce0ffff750df345652a8db05d95297a02"
ORIGINALS = {
    "resources.json": (8467, "d3f4ad669781f0364e8c2bd13f8ebe0a797fa362cc5e6dc3d7ad94b4db782e37"),
    "run.json": (672, "5decd9419c40f3038de3df340225d1907d974ac1bbf73ae82837b1dda1a106cf"),
    "validation.json": (284, "523524d207a543ae290d44858325065a7f5022f921752fcd86cfae5165738051"),
    "worker.json": (12992, "b4653f26fd08ed8347ff4998d3f434f15c4b110ddba2df02c7eaa6833347b4cd"),
}


def receipt(name):
    return json.loads((EVIDENCE / name).read_bytes())


class DebugRootBootPassEvidenceTests(unittest.TestCase):
    def test_originals_and_standard_envelope_are_preserved(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()},
                         set(ORIGINALS) | {"README.md", "output.log"})
        for name, (size, digest) in ORIGINALS.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual(len(raw), size)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertEqual((EVIDENCE / "output.log").read_bytes(),
                         (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("pass", "2026-10-22T22:18:26Z", "2026-10-22T22:20:10Z"))
        self.assertEqual((run["phase"], run["task"], run["execution"]), ("P3", "T1", "software"))
        self.assertEqual(run["host"], {"provider": "github-actions", "machine": "ubuntu-22.04-x86_64",
                                      "gpus": "none", "cuda": None, "driver": None})
        self.assertFalse(run["hardware_validated"])

    def test_exact_root_success_and_all_runtime_checks(self):
        worker = receipt("worker.json")
        self.assertEqual(worker["stage"], "complete")
        self.assertEqual(worker["assembly"]["result"], "pass")
        qemu = worker["qemu"]
        self.assertEqual(qemu["stage"], "complete")
        self.assertTrue(qemu["tcg_only"])
        self.assertEqual([item["check"] for item in qemu["startup_checks"]], list(range(5)))
        self.assertTrue(all(item["result"] == "pass" for item in qemu["startup_checks"]))
        self.assertEqual(qemu["proof"], {"initrd": True, "root": True, "complete": True,
                                       "guest_failure": None, "protocol_error": None})
        self.assertEqual(qemu["terminal"], {"exit_code": 0, "failure_kind": None,
                         "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        for value in (worker, qemu, receipt("validation.json")):
            self.assertEqual(value["result"], "pass")
            self.assertTrue(value["boot_verified"])
            self.assertFalse(value["confidentiality_verified"])
        # Successful proof has no measured read-duration/count telemetry.
        self.assertNotIn("root_read", qemu["proof"])

    def test_parent_cleanup_does_not_rewrite_subordinate_worker_scope(self):
        state = receipt("resources.json")
        self.assertEqual(state["result"], "pass")
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual((state["child_exit_code"], state["oom_kill"],
                          state["populated_before_cleanup"]), (0, 0, 0))
        self.assertEqual(state["memory_peak_bytes"], 2147483648)
        self.assertEqual(state["limits"], {"cpu.max": "200000 100000", "memory.max": "2147483648",
                         "memory.oom.group": "1", "memory.swap.max": "0", "pids.max": "128"})
        self.assertFalse(receipt("worker.json")["private_work_removed"])
        self.assertTrue(receipt("validation.json")["private_images_removed"])

    def test_exact_producer_closures_and_compiler_binding(self):
        state, worker = receipt("resources.json"), receipt("worker.json")
        for label, count in (("source", 42), ("debug_source", 28)):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            self.assertEqual(len(worker[label]["sources"]), count)
            for name, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)
        compiler = worker["compiler"]
        self.assertEqual(compiler["version"], "go1.27.2")
        self.assertFalse(compiler["compiler_authenticated"])
        self.assertEqual(len(compiler["source_hashes"]), 11)
        for name, digest in compiler["source_hashes"].items():
            self.assertEqual(worker["debug_source"]["sources"][name], digest)
        assembly = worker["assembly"]
        self.assertEqual(assembly["root_hash"], worker["root_hash"])
        self.assertEqual(assembly["probe"], compiler["probe"])
        self.assertEqual(assembly["original_uki"], worker["input_files"]["nib.efi"])
        self.assertNotEqual(assembly["uki"]["image"]["sha256"], assembly["original_uki"]["sha256"])
        self.assertEqual(worker["input_files"]["root.raw"]["bytes"], 928968704)
        self.assertFalse(assembly["signed"] or assembly["uki"]["signed"])
        self.assertEqual(receipt("validation.json"), {
            "schema": "nib.debug-boot-validation/v1", "result": "pass", "boot_verified": True,
            "private_images_removed": True, "metadata_only": True, "confidentiality_verified": False,
            "production_service_verified": False, "reproducibility_verified": False, "signed": False})


if __name__ == "__main__":
    unittest.main()
