# SPDX-License-Identifier: Apache-2.0
"""Original network observations retain their distinct results and scope."""

import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/p2/2026-10-25-selected-nat-policy-failure"
SPEC = importlib.util.spec_from_file_location("nat_evidence_producer", ROOT / "scripts/prove-selected-nat.py")
PROOF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


class SelectedNatEvidenceTests(unittest.TestCase):
    def original(self, name, digest):
        raw = (EVIDENCE / name).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        return json.loads(raw)

    def assert_failed_policy(self, outer):
        self.assertEqual(outer["result"], "fail")
        self.assertIs(outer["namespace_reaped"], True)
        for key in ("gpu_used", "deployment_eligible", "published"):
            self.assertIs(outer[key], False)
        inner = outer["observation"]
        PROOF.check_report(inner)
        self.assertEqual((inner["result"], inner["stage"]), ("fail", "policy"))
        self.assertNotIn("observations", inner)
        self.assertEqual(len(inner["positive_controls"]), 15)
        self.assertTrue(all(inner["positive_controls"].values()))
        for key in ("cleanup_confirmed", "policy_removed", "gpu_used", "deployment_eligible", "confidential", "published"):
            self.assertIs(inner[key], False)

    def test_original_bytes_and_explicit_failure_are_preserved(self):
        hashes = {
            "observation.json": "3d5677a9d92b086cc7ecdca7e136382893d0c3db4153e384677dc0e0f7f8488e",
            "bootstrap-check.json": "2e22df142f51f70e8a4b35e802b1ce2f80136ce812e3e44f4aab685b41760cfa",
            "bootstrap-install.json": "814103496d33e9f10cb39ba90618b32d3184a1d0efd0367e0392611623150110",
        }
        rows = {name: self.original(name, digest) for name, digest in hashes.items()}
        outer = rows["observation.json"]
        self.assertEqual(outer["git_sha"], "742c6a1e4f6920a3001415aa75ee5fd8b2a4a299")
        self.assert_failed_policy(outer)
        self.assertNotIn("policy_step", outer["observation"])
        for name, installed in (("bootstrap-check.json", False), ("bootstrap-install.json", True)):
            row = rows[name]
            self.assertEqual(row["result"], "pass")
            self.assertEqual(row["revision"], outer["git_sha"])
            self.assertIs(row["installed"], installed)
            for key in ("daemon_started", "namespace_proof_executed", "venue_qualified"):
                self.assertIs(row[key], False)

    def test_readback_failure_preserves_source_binding_without_diagnosis(self):
        outer = self.original("readback-observation.json",
                              "3797ef9d3264a547a7d5fc484341ec1fdee77cb966e8aa21eff941c5411bfd5c")
        prior = self.original("observation.json",
                              "3d5677a9d92b086cc7ecdca7e136382893d0c3db4153e384677dc0e0f7f8488e")
        self.assertEqual(outer["git_sha"], "c82b983ef950b24cec820647a010dc6afb7c8211")
        self.assert_failed_policy(outer)
        self.assertEqual(outer["observation"]["policy_step"], "readback")
        self.assertEqual(outer["archive_sha256"], prior["archive_sha256"])
        for key in ("image_config", "reference_manifest", "engine_version", "positive_controls"):
            self.assertEqual(outer["observation"][key], prior["observation"][key])
        expected_sources = dict(prior["source_sha256"])
        expected_sources["scripts/prove-selected-nat.py"] = (
            "7c0cffc1721b397dcd86698b50d04441d7e3c7490f48629412b87b05db636252")
        self.assertEqual(outer["source_sha256"], expected_sources)

    def test_pass_is_original_and_binds_the_same_closed_cpu_experiment(self):
        raw = (ROOT / "evidence/p2/2026-10-25-selected-nat-pass/observation.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "ea6157e42ab971be3b68fea1a416984d88176aa167611ee707f0162c749ac7fe")
        outer = json.loads(raw)
        prior = self.original("readback-observation.json",
                              "3797ef9d3264a547a7d5fc484341ec1fdee77cb966e8aa21eff941c5411bfd5c")
        self.assertEqual(outer["git_sha"], "bc271d6b36a91ebb4b7a2a43a8733b7826c4b000")
        self.assertEqual((outer["schema"], outer["result"], outer["stage"]),
                         ("nib.selected-nat-proof-host/v1", "pass", "complete"))
        self.assertIs(outer["namespace_reaped"], True)
        for key in ("gpu_used", "deployment_eligible", "published"):
            self.assertIs(outer[key], False)
        for key in ("archive_sha256", "host_python", "unshare_version"):
            self.assertEqual(outer[key], prior[key])
        elapsed = (datetime.fromisoformat(outer["ended_at"]) -
                   datetime.fromisoformat(outer["started_at"])).total_seconds()
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 480 + 90 + 25)
        expected_sources = dict(prior["source_sha256"])
        expected_sources["scripts/selected_network_policy.py"] = (
            "1dece1db381cf362c63eb363a0f0f5d61d476aa0bb3e285fdf076e0927361414")
        self.assertEqual(outer["source_sha256"], expected_sources)
        inner = outer["observation"]
        PROOF.check_report(inner)
        self.assertEqual((inner["result"], inner["stage"]), ("pass", "complete"))
        for key in ("engine_version", "image_config", "reference_manifest", "positive_controls"):
            self.assertEqual(inner[key], prior["observation"][key])
        self.assertEqual(len(inner["positive_controls"]), 15)
        self.assertTrue(all(inner["positive_controls"].values()))
        self.assertEqual(set(inner["observations"]), set(inner["positive_controls"]))
        self.assertEqual({name for name, value in inner["observations"].items() if value},
                         {"tls_host", "tls_forward"})
        for key in ("namespace_verified", "dns_relay_unchanged", "tls_verified",
                    "policy_removed", "cleanup_confirmed"):
            self.assertIs(inner[key], True)
        for key in ("gpu_used", "deployment_eligible", "confidential", "published"):
            self.assertIs(inner[key], False)

    def test_ubuntu26_pass_is_original_and_keeps_ordinary_scope(self):
        raw = (ROOT / "evidence/p2/2026-10-25-selected-nat-ubuntu26-pass/observation.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         "b17eeddba18c830a71db0527b9f36397f8a3ec4a9666c75bef81d66f37085da1")
        outer = json.loads(raw)
        self.assertEqual(outer["git_sha"], "74d10833fbefdea1a7d4ac03e1db34ffdf776b37")
        self.assertEqual((outer["schema"], outer["result"], outer["stage"]),
                         ("nib.selected-nat-proof-host/v1", "pass", "complete"))
        self.assertEqual((outer["venue_profile"], outer["host_python"], outer["unshare_version"]),
                         ("ubuntu26-ordinary", "3.14.4", "2.41.3"))
        self.assertEqual(outer["archive_sha256"],
                         "b741f35a8a651f06f150ac747be6277298cd0726939222d363f9e2a1cf4cb170")
        sources = {
            "scripts/e2e/snapshot.py": "e73a5a1e3ddb7a6e7b20afce39f7c080a0317d78340f224b2bd95f705bccb59c",
            "scripts/fixtures/selected-nat-ubuntu26.json": "624e5311d149387744297d5c3c6ce47115216f35c43a515033ba730cdea1ee97",
            "scripts/fixtures/selected_nat_fixture.py": "3eeb0fb6dc3d05be9818c715f0b19ac19c8f0dab96e8eb3612d69745d636a24a",
            "scripts/prove-selected-nat.py": "64dfd23d7f254e2b1fa4641a5096fc7ff0dc81a59459bed7816861cdaee62597",
            "scripts/selected_network_policy.py": "05f84c0dfeb846a8d15fc2f694bfb7d2a5c5c5a5dffcfc7578ec7722fe8207c9",
        }
        self.assertEqual(outer["source_sha256"], sources)
        self.assertEqual(outer["venue_manifest_sha256"], sources[PROOF.VENUE_FILE])
        self.assertIs(outer["namespace_reaped"], True)
        for key in ("gpu_used", "deployment_eligible", "published"):
            self.assertIs(outer[key], False)
        elapsed = (datetime.fromisoformat(outer["ended_at"]) -
                   datetime.fromisoformat(outer["started_at"])).total_seconds()
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 480 + 90 + 25)
        inner = outer["observation"]
        PROOF.check_report(inner)
        self.assertEqual((inner["result"], inner["stage"], inner["venue_profile"]),
                         ("pass", "complete", "ubuntu26-ordinary"))
        self.assertEqual(inner["engine_version"], "29.1.3")
        self.assertEqual(inner["image_config"],
                         "sha256:64d91f7b885eed272bba87909446b12ff408d4aaa5f1a0e9ca787bbea1a020b9")
        self.assertEqual(inner["reference_manifest"],
                         "sha256:6e13e65c55e33adf203d77ee371cf8bf5d81bd4902ef07565721f46bf44917af")
        self.assertEqual(inner["policy_sha256"],
                         "c73da809f2b4e9d2570f9a7539788af41282505b1576e54bd18125fd4b16c13e")
        self.assertEqual(len(inner["positive_controls"]), 15)
        self.assertTrue(all(inner["positive_controls"].values()))
        self.assertEqual(set(inner["observations"]), set(inner["positive_controls"]))
        self.assertEqual({key for key, value in inner["observations"].items() if value},
                         {"tls_host", "tls_forward"})
        for key in ("namespace_verified", "dns_relay_unchanged", "tls_verified",
                    "policy_removed", "cleanup_confirmed"):
            self.assertIs(inner[key], True)
        for key in ("gpu_used", "deployment_eligible", "confidential", "published"):
            self.assertIs(inner[key], False)


if __name__ == "__main__":
    unittest.main()
