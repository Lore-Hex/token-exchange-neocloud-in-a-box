# SPDX-License-Identifier: Apache-2.0
"""CPU-only profile and tamper tests; these do not measure GPU throughput."""

import ast
import copy
import hashlib
import itertools
import json
import sys
import types
import unittest
from unittest import mock

import deepseek_tuning as tuning
import test_deepseek_model_profile as fixtures
from fixtures import vllm_cudagraph as graphs
from test_evidence_history import git

ROOT, host, lab = fixtures.ROOT, fixtures.host, fixtures.lab
BASE = "aa7466cbcf7321a5e012091d84fca6a2f5e13531"


class TuningProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OrdinaryUncensored.setUpClass()
        cls.args = list(fixtures.OrdinaryUncensored.args)
        cls.args[3] = {**cls.args[3], host.TUNING: (ROOT / host.TUNING).read_bytes()}
        cls.plan = fixtures.PayloadProfiles.fixture.builder.recorder.prep.launch_plan()
        cls.baseline_files, cls.baseline = host.make_payload(*cls.args, model_profile="uncensored")

    def payload(self, name, **options):
        return host.make_payload(*self.args, model_profile="uncensored", execution_profile=name, **options)

    def validate(self, files, contract):
        with mock.patch.object(lab, "read_source", side_effect=lambda path, *_: files[path.removeprefix("/opt/nib/")]):
            return lab.validate_settings(contract, self.plan, 2)

    def test_default_payload_and_args_unchanged(self):
        old = types.ModuleType("previous_host")
        old.__file__ = str(ROOT / host.SCRIPT)
        exec(compile(git(ROOT, "show", BASE + ":" + host.SCRIPT), old.__file__, "exec"), old.__dict__)
        # Hold helper bytes constant while comparing legacy builder behavior.
        old.MODEL_PROFILE_SHA = host.MODEL_PROFILE_SHA
        for args, options in ((self.args, {"model_profile": "uncensored"}),
                              (fixtures.PayloadProfiles.args, {}),
                              (fixtures.PayloadProfiles.args, fixtures.PayloadProfiles.options)):
            with self.subTest(options=options):
                self.assertEqual(old.make_payload(*args, **options), host.make_payload(*args, **options))
        self.assertEqual(self.payload(None), (self.baseline_files, self.baseline))
        self.assertNotIn("execution_profile", self.baseline)
        self.assertNotIn("tuning.py", self.baseline_files)
        self.assertIsNone(tuning.enabled(self.baseline))
        self.assertEqual(self.validate(self.baseline_files, self.baseline), fixtures.NEW)

    def test_only_profile_arguments_and_binding_change(self):
        for name in tuning.NAMES:
            files, contract = self.payload(name)
            with self.subTest(name=name):
                self.assertEqual(self.validate(files, contract), fixtures.NEW)
                self.assertEqual(tuning.enabled(contract), name)
                self.assertEqual(contract["arguments"], tuning.arguments(self.baseline["arguments"], name))
                self.assertEqual({k: v for k, v in contract.items() if k not in
                                 ("arguments", "sources", "execution_profile")},
                                 {k: v for k, v in self.baseline.items() if k not in ("arguments", "sources")})
                self.assertEqual(contract["sources"], {**self.baseline["sources"],
                                  "tuning.py": hashlib.sha256(files["tuning.py"]).hexdigest()})
                for path in self.baseline_files.keys() - {"lab.json", "python"}:
                    self.assertEqual(files[path], self.baseline_files[path])
                self.assertNotEqual(files["python"], self.baseline_files["python"])
                self.assertFalse(contract["deployable"])
                self.assertFalse(contract["confidential"])
                with fixtures.payload_tree(files) as root:
                    self.assertEqual(host.memory_gib(contract, root), 512)
                    command = host.create_command("owned", "owner", root, "/weights", contract,
                                                  {"config": {"Env": []}})
                    self.assertEqual(command[command.index("--network") + 1], "none")
                    self.assertEqual(command[command.index("--memory-swap") + 1], "512g")

    def test_graph_sizes_and_real_drafting_are_bounded(self):
        original = copy.deepcopy(self.baseline["arguments"])
        for name in tuning.NAMES:
            args = tuning.arguments(original, name)
            flags = [x for x in args if x.startswith("--")]
            self.assertEqual(len(flags), len(set(flags)))
            for flag, value in (("--max-num-seqs", "8"), ("--max-num-batched-tokens", "4096"),
                                ("--max-model-len", "4096"), ("--tensor-parallel-size", "4"),
                                ("--data-parallel-size", "2"), ("--engram-config", '{"cpu_offload":false}')):
                self.assertEqual(args[args.index(flag) + 1], value)
            self.assertEqual("--enforce-eager" in args, name == "batch8-eager")
            self.assertEqual("--speculative-config" in args, name == "batch8-dspark")
            compilation = json.loads(args[args.index("--compilation-config") + 1])
            self.assertIs(compilation["pass_config"]["fuse_allreduce_rms"], False)
            if name != "batch8-eager":
                self.assertEqual(compilation["cudagraph_mode"], "FULL_DECODE_ONLY")
                self.assertEqual(compilation["cudagraph_capture_sizes"],
                                 [6, 12, 24, 40, 48] if name == "batch8-dspark" else [1, 2, 4, 8])
            if name == "batch8-dspark":
                spec = json.loads(args[args.index("--speculative-config") + 1])
                self.assertEqual(spec, {"method": "dspark", "num_speculative_tokens": 5,
                                       "draft_sample_method": "probabilistic", "rejection_sample_method": "block",
                                       "enable_adaptive_verification": True})
                self.assertNotIn("model", spec)
        self.assertEqual(original, self.baseline["arguments"])

    def test_tuned_measurements_use_identical_workload_and_guards(self):
        options = fixtures.PayloadProfiles.options
        for profile in (None, fixtures.stream_lab.EXTENDED_PROFILE):
            baseline_files, baseline = self.payload(None, **options, stream_profile=profile)
            for name in tuning.NAMES:
                files, contract = self.payload(name, **options, stream_profile=profile)
                with self.subTest(name=name, profile=profile):
                    self.assertEqual(self.validate(files, contract), fixtures.NEW)
                    self.assertEqual(tuning.enabled(contract), name)
                    for check in (fixtures.parity.enabled, fixtures.observation.enabled, fixtures.stream_lab.enabled):
                        check(contract)
                    fixtures.stream_lab.load(files, contract["sources"])
                    for key in options.keys() | {"stream_cohort", "base_image", "pack_revision", "pack_bytes",
                                                 "environment", "interpreter", "manifest_sha256"}:
                        self.assertEqual(contract[key], baseline[key])
                    for path in baseline_files.keys() - {"lab.json", "python"}:
                        self.assertEqual(files[path], baseline_files[path])
                    self.assertFalse(contract["confidential"])
                    self.assertFalse(contract["deployable"])
                    with fixtures.payload_tree(files) as root:
                        command = host.create_command("owned", "owner", root, "/weights", contract,
                                                      {"config": {"Env": []}})
                        self.assertEqual(command[command.index("--network") + 1], "none")
                        self.assertEqual(command[command.index("--memory") + 1], "512g")
                        self.assertEqual(command[command.index("--memory-swap") + 1], "512g")
                    for key in options:
                        bad = copy.deepcopy(contract)
                        del bad[key]
                        with self.assertRaises(ValueError): self.validate(files, bad)
                        bad = {**contract, key: False}
                        with self.assertRaises(ValueError): self.validate(files, bad)
                    for key, value in (("provider_conformance", {}), ("execution_profile", "unknown"),
                                       ("gpu_security", "confidential"), ("data_parallel_size", 1)):
                        with self.assertRaises(ValueError): self.validate(files, {**contract, key: value})
                    with self.assertRaises(ValueError):
                        self.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)

    def test_tuning_cli_admits_only_complete_measurement_selection(self):
        names = tuple(fixtures.PayloadProfiles.options)
        command = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                   "--model-profile", "uncensored", "--execution-profile", "batch8-dspark"]
        for choices in itertools.product((False, True), repeat=len(names)):
            flags = ["--" + name.replace("_", "-") for name, chosen in zip(names, choices) if chosen]
            with self.subTest(choices=choices), mock.patch.object(sys, "argv", command + flags), \
                    mock.patch.object(host, "prepare") as prepare:
                if all(choices) or not any(choices):
                    self.assertEqual(host.main(), 0)
                    prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                        model_profile="uncensored", execution_profile="batch8-dspark",
                        **{name: True for name, chosen in zip(names, choices) if chosen})
                else:
                    with self.assertRaises(ValueError): host.main()
                    prepare.assert_not_called()
        for value in (None, 1, "true"):
            with self.assertRaises(ValueError):
                host.tuning_measurement_options(value, True, True, True, None, None)

    def test_graph_dispatch_covers_target_and_drafter_through_eight_requests(self):
        def manager(sizes, query_len, varlen):
            state = types.SimpleNamespace(
                compilation_config=types.SimpleNamespace(
                    cudagraph_capture_sizes=sizes, max_cudagraph_capture_size=max(sizes)),
                cudagraph_mode=graphs.CUDAGraphMode.FULL_DECODE_ONLY,
                max_num_reqs=8, decode_query_len=query_len, varlen_decode=varlen,
                lora_capture_cases=[0], vllm_config=types.SimpleNamespace(
                    speculative_config=types.SimpleNamespace(uses_dynamic_speculative_decoding=lambda: False)),
                _maybe_ubatch_twin=lambda desc: None, _resolve_effective_loras=lambda n: n,
                _capture_descs={}, _candidates={}, _graphs_captured=True)
            graphs._init_candidates(state)
            return state

        def mode(state, n, query_len):
            return graphs.dispatch(state, n, n * query_len, query_len, 0, query_len).cg_mode

        for name in ("batch8-graphs", "batch8-dspark"):
            args = tuning.arguments(self.baseline["arguments"], name)
            sizes = json.loads(args[args.index("--compilation-config") + 1])["cudagraph_capture_sizes"]
            stages = ((6, True), (5, False)) if name == "batch8-dspark" else ((1, False),)
            for query_len, varlen in stages:
                state = manager(sizes, query_len, varlen)
                for n in range(1, 9):
                    # Adaptive verification also uses shorter target queries.
                    for actual_len in range(1 if varlen else query_len, query_len + 1):
                        with self.subTest(name=name, requests=n, stage=query_len, actual=actual_len):
                            self.assertEqual(mode(state, n, actual_len), graphs.CUDAGraphMode.FULL)
                self.assertEqual(mode(state, 9, query_len), graphs.CUDAGraphMode.NONE)
                self.assertTrue(all(d.num_tokens <= 8 * query_len and d.num_reqs <= 8
                                    for d in state._capture_descs[graphs.CUDAGraphMode.FULL]))
        # Regression witness: the original list fell back to eager for batches 6-8.
        old = manager([6, 12, 24, 48], 5, False)
        self.assertEqual([mode(old, n, 5) for n in range(1, 9)],
                         [graphs.CUDAGraphMode.FULL] * 5 + [graphs.CUDAGraphMode.NONE] * 3)

    def test_graph_fixture_is_unchanged_pinned_upstream_code(self):
        raw = (ROOT / "scripts/fixtures/vllm_cudagraph.py").read_text()
        expected = {
            "CUDAGraphMode": "d427649439700560464333632836b9fc20dc5a14a8285a4cf1b614d020be4c15",
            "BatchExecutionDescriptor": "8e506b7a9ed032e33cfea1d908ddbd563484820bcf95a03db2e60909ab5298f5",
            "_is_compatible": "632d7dc5ac5946d32cd83e4df96cc3fa3d8b4d620462f09d54a584bd192fc65a",
            "_init_candidates": "f6e08e07ccbd40e75f834d81d082c6e271a97f86318b67510db3ca61153fe29f",
            "dispatch": "cba5513cda0b78020bd5f282dfdfe765cc128e7ceca91054a524734a3c88ba32",
        }
        self.assertEqual({node.name: hashlib.sha256(ast.get_source_segment(raw, node).encode()).hexdigest()
                          for node in ast.parse(raw).body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                          and node.name != "round_up"}, expected)

    def test_reject_unknown_profiles_and_wrong_base_flags(self):
        for name in (True, 8, {}, [], "", "auto", "batch8-graphs --host 0.0.0.0", "../profile"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.payload(name)
        for suffix in (["--max-num-seqs", "128"], ["--enforce-eager"], ["--speculative-config", "{}"]):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                tuning.arguments(self.baseline["arguments"] + suffix, "batch8-dspark")
        for flag in ("--max-num-seqs", "--compilation-config"):
            bad = list(self.baseline["arguments"])
            bad[bad.index(flag) + 1] = "changed"
            with self.assertRaises(ValueError):
                tuning.arguments(bad, "batch8-graphs")

    def test_profile_rejects_other_hardware_models_modes_and_optional_paths(self):
        files, contract = self.payload("batch8-graphs")
        for change in ({"gpu_security": "confidential"}, {"gpu_security": None}, {"gpu_family": "B200"},
                       {"operation": "pre-serve"}, {"data_parallel_size": 1}, {"data_parallel_size": 2.0},
                       {"data_parallel_size": True}, {"model_profile": fixtures.profiles.select()},
                       {"execution_profile": None}, {"execution_profile": {}},
                       *({key: False} for key in ("tokenizer_parity", "tokenizer_materialization",
                         "runtime_observation", "stream_cohort", "provider_conformance"))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate(files, {**contract, **change})
        for security, model in (("confidential", "uncensored"), ("nonconfidential", "original")):
            args = list(self.args)
            args[7] = security
            with self.subTest(security=security, model=model), self.assertRaises(ValueError):
                host.make_payload(*args, model_profile=model, execution_profile="batch8-eager")

    def test_tampered_profile_source_and_arguments_fail_closed(self):
        files, contract = self.payload("batch8-dspark")
        with self.assertRaises(ValueError):
            self.validate({**files, "tuning.py": files["tuning.py"] + b"\n"}, contract)
        missing = copy.deepcopy(contract)
        del missing["execution_profile"]
        with self.assertRaises(ValueError):
            self.validate(files, missing)
        with self.assertRaises(ValueError):
            tuning.enabled(missing)
        for flag, value in (("--max-num-seqs", "256"), ("--max-model-len", "1048576"),
                            ("--host", "0.0.0.0"), ("--engram-config", '{"cpu_offload":true}'),
                            ("--speculative-config", '{"rejection_sample_method":"synthetic"}')):
            bad = copy.deepcopy(contract)
            bad["arguments"][bad["arguments"].index(flag) + 1] = value
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                self.validate(files, bad)
        with self.assertRaises(ValueError):
            self.validate(files, {**contract, "environment": {**contract["environment"], "VLLM_CONFIDENTIAL_COMPUTE": "0"}})

    def test_source_verification_rejects_mismatch_and_missing_files(self):
        data = {path: ("synthetic source " + path).encode() for path in tuning.SOURCES}
        hashes = {path: hashlib.sha256(raw).hexdigest() for path, raw in data.items()}
        reader = mock.Mock(side_effect=lambda path, limit: data[path.removeprefix("/selected/")])
        with mock.patch.object(tuning, "SOURCES", hashes):
            tuning.verify_sources(reader, "/selected")
            self.assertEqual(reader.call_args_list, [mock.call("/selected/" + path, 256 << 10) for path in hashes])
            for path in data:
                with self.subTest(path=path), mock.patch.dict(data, {path: b"changed"}), self.assertRaises(ValueError):
                    tuning.verify_sources(reader, "/selected")
            reader.side_effect = FileNotFoundError
            with self.assertRaises(FileNotFoundError):
                tuning.verify_sources(reader, "/selected")

    def test_cli_opt_in_is_prepare_only_and_ordinary_only(self):
        base = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                "--model-profile", "uncensored", "--execution-profile", "batch8-dspark"]
        with mock.patch.object(sys, "argv", base), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                                            model_profile="uncensored", execution_profile="batch8-dspark")
        invalid = [base + [flag] for flag in ("--tokenizer-parity", "--runtime-observation", "--stream-cohort")]
        invalid += [[host.SCRIPT, "--prepare-local-correctness-b300-dp2", *base[2:]],
                    [host.SCRIPT, "--run", "/unused", "--execution-profile", "batch8-dspark"]]
        for argv in invalid:
            with self.subTest(argv=argv), mock.patch.object(sys, "argv", argv), \
                    mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
