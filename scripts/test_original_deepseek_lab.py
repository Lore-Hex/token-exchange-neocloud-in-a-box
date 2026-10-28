# SPDX-License-Identifier: Apache-2.0
"""Official-checkpoint lab boundaries only; no GPU or live service execution."""

import copy
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import deepseek_model_profile as models
import deepseek_tuning as tuning
import engine_runtime_observation as observation
import engine_tokenizer_parity as parity
import selected_service_engine as service_engine
import test_deepseek_lab as lab_fixtures
import test_deepseek_model_profile as model_fixtures
import test_deepseek_tuning as fixtures
import test_engine_runtime_observation as observation_fixtures

host, lab, ROOT = fixtures.host, fixtures.lab, fixtures.ROOT
PROFILE = tuning.ORIGINAL_MILLION_PROFILE
MODEL = models.select("original")
OPTIONS = {"tokenizer_parity": True, "tokenizer_materialization": True,
           "runtime_observation": True}


class OriginalLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.TuningProfiles.setUpClass()
        cls.args = list(fixtures.TuningProfiles.args)
        cls.args[3] = dict(cls.args[3])
        for suffix in ("pack.yaml", "files.sha256"):
            name = MODEL["pack"] + "/" + suffix
            cls.args[3][name] = (ROOT / name).read_bytes()
        cls.plan = fixtures.TuningProfiles.plan

    def payload(self, **options):
        return host.make_payload(*self.args, model_profile="original",
                                 execution_profile=PROFILE, **{**OPTIONS, **options})

    def validate(self, files, contract):
        with mock.patch.object(lab, "read_source",
                side_effect=lambda path, *_: files[path.removeprefix("/opt/nib/")]):
            self.assertEqual(lab.validate_settings(contract, self.plan, 2), MODEL)
        with model_fixtures.payload_tree(files) as root:
            self.assertEqual(host.validate_model_profile(contract, root), MODEL)
            self.assertEqual(host.memory_gib(contract, root), 512)

    def test_explicit_pinned_identity_and_source_bound_roundtrip(self):
        files, contract = self.payload()
        self.validate(files, contract)
        self.assertEqual(models.validate_contract(contract), MODEL)
        self.assertEqual(contract["model_profile"], MODEL)
        self.assertEqual(contract["pack_revision"], MODEL["hf_revision"])
        self.assertEqual(contract["pack_bytes"], MODEL["total_bytes"])
        self.assertEqual(contract["manifest_sha256"], MODEL["manifest_sha256"])
        self.assertEqual(files["model-profile.py"], (ROOT / host.MODEL_PROFILE).read_bytes())
        self.assertEqual(host.digest(files["model-profile.py"]), host.MODEL_PROFILE_SHA)
        self.assertEqual(files["pack/files.sha256"], (ROOT / MODEL["pack"] / "files.sha256").read_bytes())
        self.assertEqual(tuning.enabled(contract), PROFILE)
        self.assertTrue(parity.enabled(contract))
        self.assertTrue(observation.enabled(contract))
        self.assertEqual(lab_fixtures.probe.parallel_size(contract), 2)
        self.assertEqual(tuning.context_limit(contract), 1048576)
        self.assertFalse(contract["deployable"])
        self.assertFalse(contract["confidential"])
        # Public serving support must be implemented and reviewed separately.
        with self.assertRaises(ValueError):
            service_engine.canonical_arguments(contract)

    def test_same_source_backed_flags_without_model_substitution(self):
        files, contract = self.payload()
        selected_files, selected = host.make_payload(*self.args, model_profile="uncensored",
            execution_profile=tuning.MILLION_PROFILE, **OPTIONS)
        arguments = list(selected["arguments"])
        arguments[arguments.index("--served-model-name") + 1] = MODEL["served_model_name"]
        self.assertEqual(contract["arguments"], arguments)
        for flag in ("--enable-prefix-caching", "--enable-prompt-tokens-details", "--enable-chunked-prefill"):
            self.assertEqual(arguments.count(flag), 1)
        self.assertEqual(arguments[arguments.index("--max-num-seqs") + 1], "1")
        self.assertEqual(arguments[arguments.index("--tensor-parallel-size") + 1], "4")
        self.assertEqual(arguments[arguments.index("--data-parallel-size") + 1], "2")
        self.assertNotEqual(files["pack/files.sha256"], selected_files["pack/files.sha256"])
        self.assertNotEqual(contract["pack_revision"], selected["pack_revision"])

    def test_no_implicit_original_or_cross_model_tuning(self):
        for name in (None, *[n for n in tuning.NAMES if n != PROFILE]):
            with self.subTest(name=name), self.assertRaises(ValueError):
                host.make_payload(*self.args, model_profile="original", execution_profile=name, **OPTIONS)
        with self.assertRaises(ValueError):
            host.make_payload(*self.args, model_profile="uncensored", execution_profile=PROFILE, **OPTIONS)
        self.assertEqual(models.validate_contract({}), MODEL)

    def test_identity_tampering_and_confidential_crossover_rejected(self):
        files, contract = self.payload()
        changes = [{"pack_revision": "0" * 40}, {"pack_bytes": MODEL["total_bytes"] - 1},
            {"manifest_sha256": "0" * 64}, {"model_profile": None},
            {"model_profile": models.select("uncensored")}, {"gpu_security": "confidential"},
            {"gpu_family": "B200"}, {"operation": "pre-serve"}, {"data_parallel_size": True},
            {"data_parallel_size": 1}, {"stream_cohort": {}}, {"provider_conformance": {}},
            {"tau_owner": {}}, {"execution_profile": tuning.MILLION_PROFILE}]
        changes += [{"model_profile": {**MODEL, key: "changed" if type(value) is str else value + 1}}
                    for key, value in MODEL.items()]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(files, {**contract, **change})
        with model_fixtures.payload_tree(files) as root:
            path = root / "payload/pack/files.sha256"
            path.write_bytes((ROOT / models.select("uncensored")["pack"] / "files.sha256").read_bytes())
            with self.assertRaises(ValueError):
                host.validate_model_profile(contract, root)

    def test_measurement_prerequisites_are_required(self):
        for key in OPTIONS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.payload(**{key: False})
        for change in ({"stream_cohort": True}, {"tau_owner": True}, {"provider_conformance": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.payload(**change)

    def test_cgroup_limits_and_observation_identity_remain_closed(self):
        _, contract = self.payload()
        sample = observation_fixtures.sample()
        sample["exe_sha256"] = contract["interpreter"]["sha256"]
        observation.validate_sample(sample, contract)
        for key, value in (("memory.max", 1024 << 30), ("memory.swap.max", 1)):
            changed = copy.deepcopy(sample)
            changed["cgroup"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                observation.validate_sample(changed, contract)
        with self.assertRaises(ValueError):
            observation.validate_sample(sample, {**contract, "gpu_security": "confidential"})

    def test_cli_explicit_original_selection_and_missing_flags(self):
        command = [host.SCRIPT, "--prepare-nonconfidential-original-b300-dp2", "/unused",
                   "--model-profile", "original", "--execution-profile", PROFILE,
                   "--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"]
        with mock.patch.object(sys, "argv", command), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                model_profile="original", execution_profile=PROFILE, **OPTIONS)
        for flag in ("--model-profile", "--execution-profile", "--tokenizer-parity",
                     "--tokenizer-materialization", "--runtime-observation"):
            changed = list(command)
            index = changed.index(flag)
            del changed[index:index + (2 if flag in ("--model-profile", "--execution-profile") else 1)]
            with self.subTest(flag=flag), mock.patch.object(sys, "argv", changed), \
                    mock.patch.object(host, "prepare") as prepare:
                with self.assertRaises(ValueError):
                    host.main()
                prepare.assert_not_called()

    def test_cli_published_bundle_runs_real_profile_validation(self):
        real_load, real_read = host.load, host.read
        config = {"config": {"Env": ["LD_LIBRARY_PATH=" + lab.IMAGE_LD_PATH, "CUDA_HOME=/usr/local/cuda"]}}

        def build(_root, _revision, work, _env):
            binary = work / "synthetic-validator"
            binary.write_bytes(b"synthetic-linux-validator-not-executed")
            return binary, "a" * 64

        def load(name, path, sha):
            module = real_load(name, path, sha)
            if name == "lab_application_builder":
                module.inputs = lambda _: ("1" * 40, dict(self.args[2]), dict(self.args[3]))
                module.recorder.build_validator = build
                module.recorder.raw_metadata = lambda *_: config
            elif name == "lab_record_support":
                module.raw_metadata = lambda *_: config
            return module

        def read(path, *args):
            if Path(path).parent == ROOT / "build/engine-image-metadata":
                return b"synthetic-OCI-metadata-not-used-for-runtime"
            return real_read(path, *args)

        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary).resolve() / "bundle"
            command = [host.SCRIPT, "--prepare-nonconfidential-original-b300-dp2", str(root),
                       "--model-profile", "original", "--execution-profile", PROFILE,
                       "--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"]
            stack.enter_context(mock.patch.object(sys, "argv", command))
            stack.enter_context(mock.patch.object(host, "load", side_effect=load))
            stack.enter_context(mock.patch.object(host, "read", side_effect=read))
            stack.enter_context(mock.patch.object(host, "committed",
                side_effect=lambda _b, _r, name: (ROOT / name).read_bytes()))
            stack.enter_context(mock.patch("builtins.print"))
            self.assertEqual(host.main(), 0)
            receipt_sha = host.digest(real_read(root / "receipt.json"))
            receipt, contract, _, _ = host.bundle_inputs(root, receipt_sha)
            self.assertEqual(contract["model_profile"], MODEL)
            self.assertEqual(receipt["source_bundle_sha256"], contract["source_bundle_sha256"])
            self.assertEqual(contract["execution_profile"], PROFILE)
            self.assertFalse(contract["deployable"])
            for name in ("probe.py", "model-profile.py", "lab.json", "pack/files.sha256"):
                path = root / "payload" / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    host.bundle_inputs(root, receipt_sha)
                path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
