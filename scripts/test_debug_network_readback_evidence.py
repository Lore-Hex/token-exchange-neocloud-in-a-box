# SPDX-License-Identifier: Apache-2.0
"""Preserve the actual fixed readback failure without inventing its comparison."""
import hashlib
from pathlib import Path
import unittest

from mkosi_export import parsed
from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p3/2026-10-23-debug-network-readback"
PRODUCER = "4f957525879bbf463db84b81e3f54ab7f053bad2"
ORIGINALS = {
    "validation.json": (285, "afa21d099f290f5aeef626cf1993efe47ee34602f24ada4ff7dd3d5d07442426"),
    "resources.json": (11604, "e1571fa4f76ef7646d24a0199af0456a281f802c13521b596268700303297fc3"),
    "worker.json": (16495, "e7f30f4dc1cbce869ce6c850ed52c7f67ab98cd214af8677562bc64e7e8a7477"),
    "run.json": (809, "9de7923522eeba5af077c1c5df3df790939299aa350b4619a36898f9e26d9e92"),
    "github-run.json": (2837, "a8f3e5a3c3d761ac81f4a1e9fe907de54771fe50dc67f50dc2d3bbfae6bc183b"),
    "github-artifacts.json": (772, "2a98fca5963f91f903833bd135bdfb32797f8c1ed75299706f4a4aecea4614c7"),
}


def receipt(name):
    return parsed((EVIDENCE / name).read_bytes())


class ReadbackFailureEvidenceTests(unittest.TestCase):
    def test_original_bytes_and_standard_envelope(self):
        self.assertEqual({p.name for p in EVIDENCE.iterdir()}, set(ORIGINALS) | {"README.md", "output.log"})
        for name, (size, digest) in ORIGINALS.items():
            raw = (EVIDENCE / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest))
        self.assertEqual((EVIDENCE / "output.log").read_bytes(), (EVIDENCE / "validation.json").read_bytes())
        self.assertEqual(producer((EVIDENCE / "run.json").read_bytes()), PRODUCER)
        run = receipt("run.json")
        self.assertEqual((run["result"], run["started_utc"], run["ended_utc"]),
                         ("fail", "2026-10-23T04:02:48Z", "2026-10-23T04:04:27Z"))
        self.assertEqual((run["phase"], run["task"], run["execution"]), ("P3", "T1", "software"))
        self.assertFalse(run["hardware_validated"])

    def test_failure_is_not_retroactively_promoted_or_localized(self):
        worker = receipt("worker.json")
        self.assertEqual((worker["stage"], worker["assembly"]["result"]), ("boot", "pass"))
        self.assertEqual(worker["qemu"]["proof"], {"initrd": True, "root": False, "complete": False,
            "guest_failure": "NETWORK_SERVICE", "protocol_error": None,
            "network_failure": {"service_result": "exit-code", "exit_code": "exited",
                                "exit_status": 76, "stage": "readback"}})
        self.assertEqual(worker["qemu"]["terminal"], {"exit_code": 0, "failure_kind": None,
                         "process_group_cleaned": True, "stderr_bytes": 0, "stdout_bytes": 0})
        self.assertEqual([x["check"] for x in worker["qemu"]["startup_checks"]], list(range(5)))
        self.assertTrue(all(x["result"] == "pass" for x in worker["qemu"]["startup_checks"]))
        for value in (worker, worker["qemu"], receipt("validation.json")):
            self.assertEqual(value["result"], "fail")
            self.assertFalse(value["boot_verified"] or value["confidentiality_verified"])
        source = git(ROOT, "show", PRODUCER + ":guest/internal/service/network_release.go").decode()
        self.assertIn("if nftables.Verify(after, destinations) != nil {\n\t\treturn failedNetworkReadback", source)

    def test_source_closures_binding_and_independent_cleanup(self):
        worker, state = receipt("worker.json"), receipt("resources.json")
        self.assertEqual(worker["source"]["profile"], "guest-network/v1")
        for label, count in (("source", 68), ("debug_source", 30)):
            self.assertEqual(state["producer"][label], worker[label])
            self.assertEqual(worker[label]["git_sha"], PRODUCER)
            self.assertEqual(len(worker[label]["sources"]), count)
            for name, digest in worker[label]["sources"].items():
                self.assertEqual(hashlib.sha256(git(ROOT, "show", PRODUCER + ":" + name)).hexdigest(), digest)
        assembly = worker["assembly"]
        self.assertEqual(assembly["root_hash"], worker["root_hash"])
        self.assertEqual(assembly["original_uki"], worker["input_files"]["nib.efi"])
        self.assertEqual(assembly["probe"], worker["compiler"]["probe"])
        self.assertFalse(assembly["signed"] or assembly["uki"]["signed"])
        for key in ("empty_verified", "group_removed", "limits_read_back"):
            self.assertTrue(state[key])
        self.assertEqual((state["child_exit_code"], state["oom_kill"], state["memory_peak_bytes"],
                          state["populated_before_cleanup"]), (1, 0, 2147483648, 0))
        self.assertEqual(state["limits"], {"cpu.max": "200000 100000", "memory.max": "2147483648",
                         "memory.oom.group": "1", "memory.swap.max": "0", "pids.max": "128"})
        self.assertFalse(worker["private_work_removed"])
        self.assertEqual(receipt("validation.json"), {"schema": "nib.debug-boot-validation/v1", "result": "fail",
            "boot_verified": False, "private_images_removed": True, "metadata_only": True,
            "confidentiality_verified": False, "production_service_verified": False,
            "reproducibility_verified": False, "signed": False})

    def test_github_binding_and_cleanup_gated_fixed_archive(self):
        run, artifacts = receipt("github-run.json"), receipt("github-artifacts.json")
        self.assertEqual((run["databaseId"], run["headSha"], run["event"], run["status"], run["conclusion"]),
                         (39897633422, PRODUCER, "workflow_dispatch", "completed", "failure"))
        steps = {x["number"]: x for x in run["jobs"][0]["steps"]}
        self.assertEqual([steps[n]["conclusion"] for n in (4, 5, 6, 7, 8, 9)],
                         ["success", "success", "failure", "skipped", "success", "skipped"])
        self.assertEqual((artifacts["total_count"], len(artifacts["artifacts"])), (1, 1))
        artifact = artifacts["artifacts"][0]
        self.assertEqual((artifact["id"], artifact["name"], artifact["size_in_bytes"]),
                         (12478110237, "native-debug-boot-39897633422-1", 11422))
        self.assertEqual(artifact["digest"], "sha256:88ac26c66b76cede0351c7ec41d3b8af7ca2a016ef0164716388a603b610ca77")
        self.assertFalse(artifact["expired"])
        self.assertEqual((artifact["workflow_run"]["id"], artifact["workflow_run"]["head_sha"]), (39897633422, PRODUCER))


if __name__ == "__main__":
    unittest.main()
