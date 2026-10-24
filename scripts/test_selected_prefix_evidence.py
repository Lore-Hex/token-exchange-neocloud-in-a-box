# SPDX-License-Identifier: Apache-2.0
"""Replay selected-checkpoint static prefix evidence, not a full tau score."""

import copy
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
REVISION = "68ab473fda5b29bf85be07099494702b63aeba69"
DIRECTORY = ROOT / "evidence/p2/2026-10-24-selected-prefix-count"
HASHES = {
    "pending.json": (637, "0c8f6259123153027743c7d4b306437e24a972916db1b87f509230c119295d16"),
    "render/run.json": (690, "84c9c5fc8ba526e313e5d8a1b15398ba79940377abb17a3e9ea58f13655cb9dd"),
    "render/output.log": (3203, "7962cbfe21b0d6f199729ed24eb1964f82df7d8f91260852cb7c1b67770f22b6"),
    "result/run.json": (980, "34214e372b789550b4ff6f3fc630e2151ce3ca94b946b555a728d8f857902e50"),
    "result/output.log": (19557, "460dc5163e4750a0ed081e52d84414d62b51664c0dd68efd9ae4c1f1ed1c612a"),
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


def source(name):
    return git(ROOT, "show", REVISION + ":scripts/" + name + ".py")


def original(name):
    module = types.ModuleType(name)
    exec(compile(source(name), name + ".py", "exec"), module.__dict__)
    return module


class SelectedPrefixEvidenceTests(unittest.TestCase):
    def test_exact_original_bytes_and_nonqualification(self):
        self.assertEqual({p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob("*") if p.is_file()}, set(HASHES))
        for name, expected in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), expected)
            if name.endswith("run.json"):
                self.assertEqual(producer(raw), REVISION)
                run = json.loads(raw)
                self.assertEqual((run["result"], run["execution"], run["host"]["gpus"]), ("pass", "software", "none"))
                self.assertIs(run["hardware_validated"], False)
        pending, render, result = (document(name) for name in ("pending.json", "render/output.log", "result/output.log"))
        self.assertEqual((pending["result"], pending["cleanup"]), ("incomplete", "unproven"))
        self.assertEqual((result["git_sha"], result["stage"], result["result"]), (REVISION, "completed", "pass"))
        self.assertEqual((result["started_utc"], result["ended_utc"]), ("2026-10-24T12:19:51Z", "2026-10-24T12:23:10Z"))
        self.assertEqual(result["render"], render)
        self.assertEqual((render["result"], render["cleanup"]), ("pass", "confirmed"))
        self.assertEqual((result["tokenizer"]["cleanup"], result["tokenizer"]["entry_pass"]), ("confirmed", True))
        # The outer CPU diagnostic is deliberately BLOCKED for deployment;
        # only its independently validated tokenizer observation passed.
        self.assertEqual((result["tokenizer"]["candidate_status"], result["tokenizer"]["result"]), ("BLOCKED", "fail"))
        for row in (render, result, result["observation"]):
            for key in ("quality_eligible", "confidential", "full_fit_proven"):
                self.assertIs(row[key], False)
        self.assertIs(result["generation_performed"], False)

    def test_original_producer_and_selected_tokenizer_binding(self):
        result = document("result/output.log")
        toolkit = result["render"]["toolkit"]
        self.assertEqual(toolkit["git_sha"], REVISION)
        for path, expected in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":" + path)).hexdigest(), expected)
        profiles = original("deepseek_model_profile")
        selected = profiles.select("uncensored")
        manifest = git(ROOT, "show", REVISION + ":" + selected["pack"] + "/files.sha256")
        self.assertEqual(profiles.validate_manifest("uncensored", manifest), selected)
        provenance = result["tokenizer"]["provenance"]
        for row in (document("pending.json"), result, provenance):
            self.assertEqual(row["model_profile"], selected)
        parity = original("engine_tokenizer_parity")
        self.assertEqual(provenance["model_files"], {name: {"bytes": size, "sha256": sha}
                         for name, (size, sha) in parity.MODEL_FILES.items()})
        for name, (size, sha) in parity.MODEL_FILES.items():
            self.assertEqual(manifest.decode("ascii").splitlines().count(f"{sha}  {size}  {name}"), 1)
        self.assertEqual(provenance["git_sha"], REVISION)
        self.assertEqual(provenance["render_plan_sha256"], result["render"]["plan_sha256"])
        self.assertEqual(provenance["request_sha256"], result["render"]["prefix"]["request_sha256"])

    def test_original_validator_and_mutations(self):
        result = document("result/output.log")
        target = original("engine_target_prefix")
        parity, correctness, _ = target.modules({name + ".py": source(module) for name, module in (
            ("tokenizer-parity", "engine_tokenizer_parity"), ("correctness", "engine_local_correctness"),
            ("agent-prefix", "tau_agent_prefix"))})
        expected = result["render"]["prefix"]["request_sha256"]
        validate = lambda value: target.validate(value, parity, correctness, expected)
        row = result["observation"]
        self.assertEqual(validate(row), result["tokenizer"]["experiment"]["entry"]["agent_prefix"])
        self.assertEqual((row["prefix_tokens"], row["output_reserve"], row["context_tokens"]), (4785, 256, 4096))
        self.assertEqual(row["prefix_tokens"] + row["output_reserve"], 5041)
        self.assertEqual(row["result"], "context-exceeded")
        self.assertEqual(len(row["controls"]["cases"]), 4)
        for mutation in ({"context_tokens": 8192}, {"output_reserve": 0}, {"boundary_verified": False},
                         {"full_fit_proven": True}, {"quality_eligible": True}, {"generation_performed": True},
                         {"confidential": True}, {"request_sha256": "0" * 64}, {"result": "full-fit-unproven"}):
            value = copy.deepcopy(row)
            value.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate(value)


if __name__ == "__main__":
    unittest.main()
