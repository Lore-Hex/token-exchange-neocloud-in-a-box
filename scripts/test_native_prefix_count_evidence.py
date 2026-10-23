# SPDX-License-Identifier: Apache-2.0
"""Original CPU prefix count excludes 4K; not full-request fit or tau execution."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from test_evidence_history import git, producer

ROOT = Path(__file__).resolve().parents[1]
REVISION = "35b5e2caef9e1113b649ae3f744b26c204867cbf"
DIRECTORY = ROOT / "evidence/p2/2026-10-23-native-prefix-count"
HASHES = {
    "pending.json": (199, "46b06123206047654e888db605d1a71ccc78a7720f2821e91f2b78788325e0c5"),
    "render/run.json": (690, "ae58948f45e643743f25ca1ef2ae9110a6d5c190ba131c0340590c8644bd317f"),
    "render/output.log": (3203, "d90662977a7039236878129486c64fc58acd53a6d9886cfc7d8350e24ac27435"),
    "result/run.json": (863, "ae8b263df427f1d146e90c5528aba602138fe03a4470a2287e8e9d33bdcac70a"),
    "result/output.log": (18778, "92b1a234e222e9e42ae5d395909ab088b0b1dc96ca0f58e5f9baefa4a861a589"),
}


def document(name):
    return json.loads((DIRECTORY / name).read_bytes())


class NativePrefixCountEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        names = (
            "scripts/record-engine-cpu-entry.py", "scripts/engine_cpu_entry.py", "scripts/engine_lab.py",
            "scripts/engine_lab_bootstrap.py", "scripts/engine_exception.py", "scripts/engine_application_progress.py",
            "scripts/record-engine-import-smoke.py", "scripts/prepare-engine-application.py",
            "scripts/engine_application.py", "scripts/engine_python_bootstrap.py", "scripts/engine_import_smoke.py",
            "scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py", "scripts/engine_overlay.py",
            "scripts/check-engine-candidate.py", "scripts/e2e/snapshot.py", "scripts/engine_image_diagnostic.py",
            "scripts/engine_python_descriptor.py")
        for name in names:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(git(ROOT, "show", REVISION + ":" + name))
        source = (root / names[0]).read_bytes()
        if hashlib.sha256(source).hexdigest() != "1996f631a0961562f7352383c741d988dd828e5960bdba230c4556d2a2de062d":
            raise AssertionError("original CPU producer changed")
        historical = types.ModuleType("native_prefix_original")
        historical.__file__ = str(root / names[0])
        exec(compile(source, historical.__file__, "exec"), historical.__dict__)
        original_git = historical.checker.git

        def source_git(repo, *args, **kwargs):
            if args == ("rev-parse", "--verify", "HEAD^{commit}"):
                return (REVISION + "\n").encode()
            return original_git(repo, *args, **kwargs)

        with mock.patch.object(historical.checker, "git", side_effect=source_git):
            revision, bundle, extra = historical.inputs(ROOT, agent_prefix=True)
            files, contract = historical.prepare_files(revision, bundle, extra)
        cls.historical, cls.bundle, cls.extra, cls.contract = historical, bundle, extra, contract
        cls.application = historical.overlay.load_json(files["application/contract.json"])
        cls.measured = historical.builder.application.measurement(historical.overlay, files["application/measurement.json"])
        cls.target = types.ModuleType("original_prefix_validator")
        exec(compile(extra["scripts/engine_target_prefix.py"], "original-prefix.py", "exec"), cls.target.__dict__)
        public_sources = {name: extra[path] for name, path in (
            ("tokenizer-parity.py", "scripts/engine_tokenizer_parity.py"),
            ("correctness.py", "scripts/engine_local_correctness.py"),
            ("agent-prefix.py", "scripts/tau_agent_prefix.py"))}
        cls.parity, cls.correctness, _ = cls.target.modules(public_sources)

    def test_original_bytes_terminal_outcomes_and_claim_limits(self):
        files = {p.relative_to(DIRECTORY).as_posix() for p in DIRECTORY.rglob("*") if p.is_file()}
        self.assertEqual(files, set(HASHES))
        for name, (size, digest) in HASHES.items():
            raw = (DIRECTORY / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (size, digest))
            if name.endswith("run.json"):
                self.assertEqual(producer(raw), REVISION)
                run = json.loads(raw)
                self.assertEqual((run["result"], run["execution"], run["host"]["gpus"]), ("pass", "software", "none"))
                self.assertIs(run["hardware_validated"], False)
        pending, render, result = (document(name) for name in ("pending.json", "render/output.log", "result/output.log"))
        self.assertEqual((pending["git_sha"], pending["result"], pending["cleanup"]), (REVISION, "incomplete", "unproven"))
        self.assertEqual((result["git_sha"], result["stage"], result["result"]), (REVISION, "completed", "pass"))
        self.assertEqual((result["started_utc"], result["ended_utc"]), ("2026-10-23T04:32:58Z", "2026-10-23T04:36:23Z"))
        self.assertEqual(result["render"], render)
        self.assertEqual((render["stage"], render["result"], render["cleanup"]), ("completed", "pass", "confirmed"))
        self.assertEqual(render["scorer_image_profile"], "docker29-descriptor")
        for name in ("2026-10-23-prefix-preparation-failure", "2026-10-23-tau-agent-prefix-local-fb41c77"):
            prior = json.loads((ROOT / "evidence/p2" / name / "render/output.log").read_bytes())
            self.assertEqual(render["prefix"], prior["prefix"])
        owner = result["tokenizer"]
        self.assertEqual((owner["stage"], owner["cleanup"], owner["result"]), ("completed", "confirmed", "fail"))
        self.assertEqual(owner["candidate_status"], "BLOCKED")
        for flag in ("deployable", "confidential", "abi_validated", "hardware_validated", "inference_started"):
            self.assertIs(owner[flag], False)
        for row in (render, result, result["observation"]):
            for flag in ("quality_eligible", "confidential", "full_fit_proven"):
                self.assertIs(row[flag], False)
        self.assertIs(result["generation_performed"], False)

    def test_original_source_and_input_bindings(self):
        result = document("result/output.log")
        toolkit = result["render"]["toolkit"]
        self.assertEqual(toolkit["git_sha"], REVISION)
        self.assertEqual(len(toolkit["files"]), 22)
        for name, digest in toolkit["files"].items():
            self.assertEqual(hashlib.sha256(git(ROOT, "show", REVISION + ":" + name)).hexdigest(), digest)
        sources = {**self.bundle, **self.extra}
        self.assertEqual(len(sources), 68)
        self.assertEqual(self.historical.checker.bundle_digest(sources),
                         "87c7fad76eb5b97c371208823fdc1bde6dd7ba0f480c7042cd4e0431257b07ca")
        provenance = result["tokenizer"]["provenance"]
        self.assertEqual(provenance["git_sha"], REVISION)
        for key in ("source_bundle_sha256", "measurement_sha256"):
            self.assertEqual(provenance[key], self.contract[key])
        self.assertEqual(provenance["requested_image"], self.contract["base_image"])
        self.assertEqual(provenance["expected_config_sha256"], self.contract["official_config"]["sha256"])
        self.assertEqual(provenance["render_plan_sha256"], result["render"]["plan_sha256"])
        self.assertEqual(provenance["request_sha256"], result["render"]["prefix"]["request_sha256"])
        self.assertEqual(provenance["model_files"], {name: {"bytes": size, "sha256": sha}
                         for name, (size, sha) in self.parity.MODEL_FILES.items()})

    def test_original_prefix_validator_and_rejection_controls(self):
        result = document("result/output.log")
        row = result["observation"]
        expected = result["render"]["prefix"]["request_sha256"]
        validate = lambda value: self.target.validate(value, self.parity, self.correctness, expected)
        self.assertEqual(validate(row), row)
        self.assertEqual(row, result["tokenizer"]["experiment"]["entry"]["agent_prefix"])
        self.assertEqual((row["prefix_tokens"], row["output_reserve"], row["context_tokens"], row["result"]),
                         (4785, 256, 4096, "context-exceeded"))
        self.assertEqual(row["prefix_tokens"] + row["output_reserve"] - row["context_tokens"], 945)
        self.assertEqual(row["tokens_sha256"], "bd6e6807cddbb0f4844b130b9d8e7632f25ebc12a367ffb9ab412d1b23340c09")
        self.assertEqual(len(row["controls"]["cases"]), 4)
        for mutation in ({"context_tokens": 16384}, {"output_reserve": 0}, {"result": "full-fit-unproven"},
                         {"boundary_verified": False}, {"full_fit_proven": True}, {"quality_eligible": True},
                         {"generation_performed": True}, {"confidential": True}, {"request_sha256": "0" * 64}):
            changed = copy.deepcopy(row)
            changed.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate(changed)

    def test_original_progress_and_entry_validators(self):
        row = document("result/output.log")["tokenizer"]["experiment"]
        cpu = self.historical.cpu
        self.assertEqual(row["phase_elapsed_ms"], {"application-preflight": 6410, "application": 165138, "entry": 23045})
        self.assertEqual((cpu.APPLICATION_SECONDS, cpu.ENTRY_SECONDS, cpu.ATTACH_SECONDS), (720, 300, 1080))
        validate = lambda value: cpu.progress.validate(value, cpu.PROGRESS_SHA, application_completed=True, elapsed_ms=171548)
        validate(row["application_progress"])
        terminal = row["application_progress"]["samples"][-1]
        self.assertEqual((terminal["inventories_started"], terminal["inventories_completed"]), (8, 8))
        for field, value in (("inventories_completed", 7), ("inventories_started", 9), ("operation", "inventory")):
            changed = copy.deepcopy(row["application_progress"])
            changed["samples"][-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(changed)
        entered = row["entry"]
        cpu.validate_finders(entered["finder_diagnostic"])
        cpu.validate_exception(entered["exception_diagnostic"])
        self.assertTrue(cpu.diagnostic_pass(entered))
        self.assertEqual(entered["checkpoints"], {"last_id": 16, "status": "eof"})
        for mutation in ({"exit_code": 1}, {"checkpoints": {"last_id": 15, "status": "eof"}}):
            self.assertFalse(cpu.diagnostic_pass({**entered, **mutation}))

    def test_original_application_validator_without_private_payload_reconstruction(self):
        applied = document("result/output.log")["tokenizer"]["experiment"]["application"]
        # Same non-import validation envelope used by the committed CPU owner.
        # No private body, complete payload reconstruction or import pass is claimed.
        def validate(value):
            envelope = {"schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
                        "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False,
                        "stage": "import-smoke", "application": value, "smoke": None,
                        "failure": "bounded-smoke-stage-rejected",
                        "phase_elapsed_ms": {"application-preflight": 0, "application": 0, "import-smoke": 0}}
            return self.historical.base.smoke.validate(self.historical.overlay, envelope, self.application, self.measured)
        self.assertFalse(validate(applied))
        for mutation in ({"stages": 4}, {"native_inventory_unchanged": False}, {"git_sha": "0" * 40},
                         {"engine_executed": True}, {"deployable": True}):
            changed = copy.deepcopy(applied)
            changed.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate(changed)


if __name__ == "__main__":
    unittest.main()
