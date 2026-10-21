# SPDX-License-Identifier: Apache-2.0
"""Guard recorded boundary claims without treating probes as image builds."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "evidence/p3"


class BoundaryEvidenceTests(unittest.TestCase):
    def evidence(self, directory):
        run = json.loads((ROOT / directory / "run.json").read_text())
        result = json.loads((ROOT / directory / "result.json").read_text())
        self.assertEqual(run["result"], "pass")
        self.assertEqual(result["result"], "pass")
        self.assertEqual(run["git_sha"], result["git_sha"])
        self.assertFalse(result["package_installation_executed"])
        return result

    def test_namespace_boundary_is_actual_but_not_an_image(self):
        result = self.evidence("2026-10-21-final-confinement")
        for key in ("final_confinement_validated", "mapped_ownership_validated",
                    "pid_namespace_validated", "host_processes_hidden",
                    "namespaces_validated", "process_cleanup_confirmed", "payload_removed"):
            self.assertIs(result[key], True)
        self.assertEqual(result["tools_checked"], 7)
        for key in ("mkosi_build_executed", "maintainer_scripts_executed", "bootable", "deployable"):
            self.assertIs(result[key], False)

    def test_resource_probe_records_limits_and_cleanup(self):
        result = self.evidence("2026-10-21-build-resource-limits")
        for key in ("limits_read_back", "worker_join_and_inheritance_verified",
                    "empty_verified", "group_removed"):
            self.assertIs(result[key], True)
        self.assertEqual(result["limits"], {"cpu.max": "200000 100000", "memory.max": "8589934592",
            "memory.oom.group": "1", "memory.swap.max": "0", "pids.max": "256"})
        self.assertEqual(result["oom_kill"], 0)
        self.assertGreaterEqual(result["memory_peak_bytes"], 32 << 20)
        for key in ("host_runtime_pinned", "image_built", "confidential_qualified"):
            self.assertIs(result[key], False)


if __name__ == "__main__":
    unittest.main()
