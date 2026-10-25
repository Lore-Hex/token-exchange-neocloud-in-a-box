# SPDX-License-Identifier: Apache-2.0
"""Original fixed tool success, strict receipt joins and no scope promotion."""

import copy
from pathlib import Path
import unittest

import selected_service_image as image
import selected_service_tool as tool
from test_evidence_history import git
from test_selected_service_smoke import smoke


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evidence/p2/2026-10-25-selected-tool-pass"
REVISION = "76013ab9d7da6a98d35d7bae0a7dc5eead494737"
HASHES = {
    "smoke.json": "d8c240a97219983d93ad382ff03e531f30f44198ba67446035e3f1a46d94c416",
    "build.json": "b7c1825e24399b1ff1e4234e7d8ce95d8f5d2eb2e09b959e91fa61577190faaf",
    "preparation.json": "0725c91777e43111617b5cc01b8853efc5fc5bb1fb8531f8915c22aef158d7f9",
}


class ToolPassEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.raw = {name: (DIRECTORY / name).read_bytes() for name in HASHES}
        self.rows = {name: image.owner.service.correctness.strict_json(raw)
                     for name, raw in self.raw.items()}

    def assert_handoff(self, row):
        prep = self.rows["preparation.json"]
        build = image.validate_build_record(self.raw["build.json"], HASHES["build.json"], prep,
            HASHES["preparation.json"], {
                "base_image": "vllm/vllm-openai@sha256:" + prep["files"]["metadata/manifest.json"]["sha256"],
                "official_config": {"sha256": prep["files"]["metadata/config.json"]["sha256"]}})
        self.assertEqual(row, {
            "schema": "nib.selected-service-smoke/v5", "mode": "tool-smoke",
            "result": "pass", "status": "PASS", "reason": "completed", "cleanup": "confirmed",
            "preflight_pass": True, "container_created": True, "runtime_started": True,
            "image_id": build["image_id"], "prepared_sha256": build["prepared_sha256"],
            "preparation_sha256": HASHES["preparation.json"], "build_sha256": HASHES["build.json"],
            "lifecycle": row["lifecycle"],
            **dict.fromkeys(("hardware_validated", "deployable", "published", "tools", "reasoning",
                             "zdr", "confidential", "e2ee"), False),
        })
        return smoke.lifecycle(image.encoded(row["lifecycle"]), tool_smoke=True,
                               prepared_sha256=build["prepared_sha256"])

    def test_original_bytes_preparation_and_historical_source(self):
        for name, expected in HASHES.items():
            self.assertEqual(image.digest(self.raw[name]), expected)
        self.assertEqual(len(self.raw["smoke.json"]), 2940)
        prep = self.rows["preparation.json"]
        self.assertEqual(prep["git_sha"], REVISION)
        self.assertEqual(len(prep["files"]), 803)
        self.assertEqual(prep["files"]["context/service/prepared.json"]["sha256"], prep["prepared_sha256"])
        for name in ("image_built", "hardware_validated", "deployable"):
            self.assertIs(prep[name], False)
        self.assertIn(REVISION, git(ROOT, "rev-list", "HEAD").decode().splitlines())
        for name in ("selected_service.py", "selected_service_process.py", "selected_service_engine.py",
                     "selected_service_image.py", "selected_service_tool.py", "selected_service_inference.py",
                     "selected_service_conformance.py"):
            source = git(ROOT, "show", REVISION + ":scripts/" + name)
            self.assertEqual(prep["files"]["context/service/scripts/" + name],
                             {"bytes": len(source), "sha256": image.digest(source)})

    def test_original_success_keeps_exact_usage_and_all_cleanup(self):
        owner = self.assert_handoff(self.rows["smoke.json"])
        observed = tool.validate_report(tool.encoded(owner["tool_roundtrip"]),
            self.rows["smoke.json"]["prepared_sha256"], owner["inference"])
        self.assertEqual((owner["status"], owner["reason"], owner["ready"], owner["cleanup_confirmed"]),
                         ("retired", "retired", True, True))
        self.assertEqual(owner["cleanup"], [{"role": role, "drained": True, "reaped": True}
                                          for role in ("tls", "guard", "engine")])
        self.assertEqual(owner["inference"]["usage"], {"prompt_tokens": 36, "completion_tokens": 13})
        self.assertIs(owner["inference"]["auth_upstream_calls_measured"], False)
        self.assertEqual(observed["usage"], {
            "call": {"prompt_tokens": 303, "completion_tokens": 41},
            "continuation": {"prompt_tokens": 364, "completion_tokens": 6}})
        self.assertEqual(observed["finish"], {"call": "tool_calls", "continuation": "stop"})
        for name in ("attempts", "checks", "resources_closed"):
            self.assertEqual(observed[name], dict.fromkeys(tool.CASES, True))
        self.assertEqual(observed["usage_unknown"], dict.fromkeys(tool.CASES, False))
        self.assertEqual(tool.contract()["retries"], 0)

    def test_wrong_joins_cleanup_and_accounting_cannot_pass(self):
        mutations = [((name,), "0" * 64) for name in
                     ("image_id", "build_sha256", "preparation_sha256", "prepared_sha256")]
        mutations += [(("cleanup",), "unconfirmed"), (("lifecycle", "cleanup_confirmed"), False)]
        mutations += [(("lifecycle", "cleanup", index, name), False)
                      for index in range(3) for name in ("drained", "reaped")]
        mutations += [(("lifecycle", "tool_roundtrip", name), "0" * 64)
                      for name in ("prepared_sha256", "contract_sha256", "inference_sha256")]
        mutations += [(("lifecycle", "tool_roundtrip", name, case), value)
                      for case in tool.CASES for name, value in (
                          ("checks", False), ("resources_closed", False), ("usage_unknown", True),
                          ("finish", "length"), ("usage", {"prompt_tokens": 303, "completion_tokens": True}))]
        for path, value in mutations:
            changed = copy.deepcopy(self.rows["smoke.json"])
            target = changed
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path), self.assertRaises((AssertionError, ValueError)):
                self.assert_handoff(changed)

    def test_claims_and_content_cannot_be_added(self):
        for path in ((), ("lifecycle",), ("lifecycle", "inference"), ("lifecycle", "tool_roundtrip")):
            for name, value in [(key, True) for key in tool.inference.CLAIMS +
                                ("published", "auth_upstream_calls_measured", "benchmark_pass")] + \
                               [("body", "PRIVATE_CANARY")]:
                changed = copy.deepcopy(self.rows["smoke.json"])
                target = changed
                for key in path:
                    target = target[key]
                target[name] = value
                with self.subTest(path=path, name=name), self.assertRaises((AssertionError, ValueError)):
                    self.assert_handoff(changed)


if __name__ == "__main__":
    unittest.main()
