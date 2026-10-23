# SPDX-License-Identifier: Apache-2.0
"""Original bounded application failure, not a tokenizer count or tau result."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
REVISION = "fb41c77ea96ae9a9d76603d5d98b69497bde80a8"
DIRECTORY = ROOT / "evidence/p2/2026-10-23-tau-agent-prefix-local-fb41c77"
HASHES = {
    "pending.json": "d94926f503217d96a77822934fde6ca376328e818e60e07f386d8507f01ff9fa",
    "render/run.json": "b389a07fc094b6aa8cc48f0df5b9b0f8cd4f95033b0e432e7d3fffb59fb7cda2",
    "render/output.log": "58eaa2d6e7f4d9222684cc4ec875d91d29f680d6d15326714b30735b6d4df442",
    "result/run.json": "1b28d2332febab05c6aa78f31d9e824933fb00ac5889e0165e3d875d2d896d5d",
    "result/output.log": "a58c723ee3cc64ca96fc8c927b5e00421a9dac2d27bddcd8df96a6f16f5da5e9",
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


class AgentPrefixEvidenceTests(unittest.TestCase):
    def test_earlier_native_failure_is_distinct_and_used_the_same_application(self):
        revision = "0ea53612f5e0b769eb2652af3be89af89678946c"
        directory = ROOT / "evidence/p2/2026-10-22-cpu-native-39795135328-1"
        values = []
        for name, digest in (("run.json", "236924530e19b6d4d01ee3d3d4aeed9906a5f834898037e0931d4c38c13af081"),
                             ("output.log", "2cf926e547a7377248eea3825e354b0ead2d228aa0d9e18e64e0852f420ba030")):
            raw = (directory / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            values.append(json.loads(raw))
        run, output = values
        self.assertEqual((run["git_sha"], output["provenance"]["git_sha"]), (revision, revision))
        self.assertEqual((run["started_utc"], run["ended_utc"]), ("2026-10-22T14:35:39Z", "2026-10-22T14:47:57Z"))
        self.assertEqual(run["host"]["provider"], "github-actions")
        self.assertEqual(run["host"]["machine"], "linux-amd64-software-runner")
        self.assertEqual(run["host"]["gpus"], "none")
        self.assertIs(run["hardware_validated"], False)
        self.assertEqual((run["result"], output["result"], output["stage"], output["cleanup"]),
                         ("fail", "fail", "completed", "confirmed"))
        experiment = output["experiment"]
        self.assertEqual((experiment["diagnostic"], experiment["stage"]), ("tokenizer-continuation", "application"))
        self.assertEqual(experiment["phase_elapsed_ms"],
                         {"application-preflight": 16982, "application": 703019, "entry": None})
        self.assertIsNone(experiment["application"])
        self.assertIsNone(experiment["entry"])
        self.assertIs(experiment["entry_pass"], False)
        current = document("result/output.log")["tokenizer"]
        for key in ("requested_image", "expected_config_sha256", "measurement_sha256"):
            self.assertEqual(output["provenance"][key], current["provenance"][key])
        for name in ("scripts/engine_application.py", "scripts/engine_overlay.py", "scripts/record-engine-import-smoke.py"):
            self.assertEqual(git(ROOT, "show", revision + ":" + name), git(ROOT, "show", REVISION + ":" + name))
        commands = []
        for source_revision in (revision, REVISION):
            raw = git(ROOT, "show", source_revision + ":scripts/record-engine-cpu-entry.py").decode()
            node = next(n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == "create_command")
            commands.append(ast.get_source_segment(raw, node))
        self.assertEqual(commands[0], commands[1])

    def test_all_original_bytes_and_distinct_render_and_final_outcomes(self):
        self.assertEqual({p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob("*") if p.is_file()}, set(HASHES))
        for name, digest in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertLessEqual(len(raw), 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        pending, render, result = (document(name) for name in ("pending.json", "render/output.log", "result/output.log"))
        self.assertEqual((pending["git_sha"], pending["result"], pending["cleanup"]), (REVISION, "incomplete", "unproven"))
        self.assertEqual((result["started_utc"], result["ended_utc"]),
                         ("2026-10-23T01:27:09Z", "2026-10-23T01:39:26Z"))
        self.assertEqual((render["started_utc"], render["ended_utc"]),
                         ("2026-10-23T01:27:15Z", "2026-10-23T01:27:23Z"))
        self.assertEqual(result["render"], render)
        self.assertEqual((render["stage"], render["result"], render["cleanup"]), ("completed", "pass", "confirmed"))
        self.assertEqual((result["stage"], result["result"], result["git_sha"]), ("tokenizer-execution", "fail", REVISION))
        self.assertEqual(render["scorer_image_profile"], "docker29-descriptor")
        self.assertEqual(result["scorer_image_profile"], render["scorer_image_profile"])
        self.assertEqual(render["prefix"], {"messages": 1, "tools": 16, "generation_performed": False,
            "request_sha256": "439f96e7c42c8db6443b1ef503959fb0bfb46792b69c70634930fae1f929f683"})
        self.assertIsNone(result["observation"])
        for prefix, expected in (("render", "pass"), ("result", "fail")):
            run = document(prefix + "/run.json")
            self.assertEqual((run["git_sha"], run["result"], run["execution"]), (REVISION, expected, "software"))
            self.assertEqual(run["host"]["gpus"], "none")
            self.assertIs(run["hardware_validated"], False)
            self.assertIn("--scorer-image-profile docker29-descriptor", run["command"])
        for row in (render, result):
            for flag in ("quality_eligible", "confidential", "full_fit_proven"):
                self.assertIs(row[flag], False)
        self.assertIs(result["generation_performed"], False)

    def test_application_deadline_observation_never_becomes_tokenizer_fit(self):
        result = document("result/output.log")
        owner, experiment = result["tokenizer"], result["tokenizer"]["experiment"]
        self.assertEqual((owner["stage"], owner["cleanup"], owner["result"]), ("completed", "confirmed", "fail"))
        self.assertNotIn("failure", owner)
        self.assertEqual((experiment["stage"], experiment["failure"], experiment["diagnostic"]),
                         ("application", "bounded-cpu-entry-rejected", "tau-agent-prefix"))
        self.assertEqual(experiment["phase_elapsed_ms"],
                         {"application-preflight": 10817, "application": 709217, "entry": None})
        self.assertIsNone(experiment["application"])
        self.assertIsNone(experiment["entry"])
        for row in (owner, experiment):
            for flag in ("entry_pass", "abi_validated", "hardware_validated", "inference_started", "deployable", "confidential"):
                self.assertIs(row[flag], False)
        provenance = owner["provenance"]
        self.assertEqual(provenance["git_sha"], REVISION)
        self.assertEqual(provenance["request_sha256"], result["render"]["prefix"]["request_sha256"])
        self.assertEqual(provenance["render_plan_sha256"], result["render"]["plan_sha256"])
        self.assertEqual(provenance["payload_sha256"], "c7cf05f6c8a6f9372e327e560d478fa47c56d9250d36f374ffbcd43f88e5888b")
        self.assertEqual(provenance["model_files"], {
            "tokenizer.json": {"bytes": 6367257, "sha256": "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b"},
            "tokenizer_config.json": {"bytes": 801, "sha256": "6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547"}})

    def test_exact_producer_sources_and_existing_failure_validator_replay(self):
        result = document("result/output.log")
        toolkit = result["render"]["toolkit"]
        self.assertEqual(toolkit["git_sha"], REVISION)
        self.assertEqual(len(toolkit["files"]), 22)
        for name, digest in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":" + name)).hexdigest(), digest)
        self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":scripts/record-tau-agent-prefix.py")).hexdigest(),
                         "c3e17283a43d469f23eca32a4d1cf5b3974c2d3b3bba20101d436840f783418a")
        names = (
            "scripts/record-engine-cpu-entry.py", "scripts/engine_cpu_entry.py", "scripts/engine_lab.py",
            "scripts/engine_lab_bootstrap.py", "scripts/engine_exception.py", "scripts/record-engine-import-smoke.py",
            "scripts/prepare-engine-application.py", "scripts/engine_application.py", "scripts/engine_python_bootstrap.py",
            "scripts/engine_import_smoke.py", "scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py",
            "scripts/engine_overlay.py", "scripts/check-engine-candidate.py", "scripts/e2e/snapshot.py",
            "scripts/engine_image_diagnostic.py", "scripts/engine_python_descriptor.py")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(git(ROOT, "show", REVISION + ":" + name))
            source = (root / names[0]).read_bytes()
            self.assertEqual(hashlib.sha256(source).hexdigest(), "74a33a4d66b0a178a533d295b174596eb5eeffdb69fbdd052feb54aadf007880")
            historical = types.ModuleType("prefix_evidence_producer")
            historical.__file__ = str(root / names[0])
            exec(compile(source, historical.__file__, "exec"), historical.__dict__)
            original_git = historical.checker.git
            def source_git(repo, *args, **kwargs):
                return (REVISION + "\n").encode() if args == ("rev-parse", "--verify", "HEAD^{commit}") else original_git(repo, *args, **kwargs)
            with mock.patch.object(historical.checker, "git", side_effect=source_git):
                revision, bundle, extra = historical.inputs(ROOT, agent_prefix=True)
                files, contract = historical.prepare_files(revision, bundle, extra)
            provenance = result["tokenizer"]["provenance"]
            self.assertEqual(contract["source_bundle_sha256"], provenance["source_bundle_sha256"])
            self.assertEqual(contract["base_image"], provenance["requested_image"])
            self.assertEqual(contract["official_config"]["sha256"], provenance["expected_config_sha256"])
            self.assertEqual(contract["measurement_sha256"], provenance["measurement_sha256"])
            overlay, cpu = historical.overlay, historical.cpu
            application = overlay.load_json(files["application/contract.json"])
            measured = historical.builder.application.measurement(overlay, files["application/measurement.json"])
            experiment = result["tokenizer"]["experiment"]
            self.assertEqual((cpu.APPLICATION_SECONDS, cpu.ENTRY_SECONDS, cpu.ATTACH_SECONDS), (720, 300, 1080))
            self.assertEqual(sum(x for x in experiment["phase_elapsed_ms"].values() if x is not None), 720034)
            # No private request body survives. Replay only the observed early
            # failure; do not pretend to reconstruct its payload or a count.
            validate = lambda row: cpu.validate(overlay, row, application, measured, historical.base.smoke, prefix_sources={})
            self.assertFalse(validate(experiment))
            for mutation in ({"entry_pass": True}, {"confidential": True}, {"stage": "complete"},
                             {"entry": {}}, {"diagnostic": "tokenizer-continuation"}):
                altered = copy.deepcopy(experiment)
                altered.update(mutation)
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    validate(altered)


if __name__ == "__main__":
    unittest.main()
