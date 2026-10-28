# SPDX-License-Identifier: Apache-2.0
"""Fixed model selection and synthetic runtime boundaries, never GPU evidence."""

import copy
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import deepseek_model_profile as profiles
import test_deepseek_lab as fixtures
from test_evidence_history import git
import stream_lab
import stream_cohort
import stream_transport
import engine_local_correctness as correctness
import engine_tokenizer_parity as parity
import engine_runtime_observation as observation
import test_engine_tokenizer_parity as parity_fixtures
import test_engine_runtime_observation as observation_fixtures
from test_stream_cohort import endpoint, fake_collection, sample, workload
from test_stream_transport import server, pieces, collect

ROOT, host, lab = fixtures.ROOT, fixtures.host, fixtures.lab
BASE = "cbe54222d18168aa37f452614fc23b280fe51869"
NEW = profiles.select("uncensored")


@contextmanager
def payload_tree(files):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        for name, raw in files.items():
            path = root / "payload" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        yield root


class FixedProfiles(unittest.TestCase):
    def test_closed_immutable_profiles_and_exact_manifests(self):
        self.assertEqual(profiles.NAMES, ("original", "uncensored"))
        for name in profiles.NAMES:
            value = profiles.select(name)
            self.assertEqual(profiles.validate_manifest(name, (ROOT / value["pack"] / "files.sha256").read_bytes()), value)
            pack = (ROOT / value["pack"] / "pack.yaml").read_text()
            for field in ("served_model_name", "hf_repo", "hf_revision", "total_bytes"):
                self.assertIn(f"{field}: {value[field]}\n", pack)
            value["served_model_name"] = "mutated"
            self.assertNotEqual(profiles.select(name), value)
            with self.assertRaises(ValueError):
                profiles.validate_manifest(name, b"changed")
        for name in (None, True, {}, "", "../uncensored", "UNCENSORED", NEW["hf_repo"]):
            with self.subTest(name=name), self.assertRaises(ValueError):
                profiles.select(name)
        with self.assertRaises(TypeError):
            profiles._PROFILES["uncensored"]["total_bytes"] = 1

    def test_profile_source_hash_is_active_not_an_unchecked_import(self):
        raw = (ROOT / host.MODEL_PROFILE).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), host.MODEL_PROFILE_SHA)
        self.assertEqual(host.selected_model(raw, "uncensored")[1], NEW)
        with self.assertRaises(ValueError):
            host.selected_model(raw + b"\n", "uncensored")

    def test_selected_tokenizer_manifest_matches_existing_source_bound_verifier(self):
        for name in profiles.NAMES:
            rows = (ROOT / profiles.select(name)["pack"] / "files.sha256").read_text().splitlines()
            manifest = {path: (int(size), sha) for sha, size, path in (row.split(maxsplit=2) for row in rows)}
            for path, expected in parity.MODEL_FILES.items():
                self.assertEqual(manifest[path], expected)


class PayloadProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PayloadTests.setUpClass()
        cls.fixture = fixtures.PayloadTests
        cls.extra = dict(cls.fixture.extra)
        for name in (host.MODEL_PROFILE, NEW["pack"] + "/pack.yaml", NEW["pack"] + "/files.sha256",
                     host.PARITY, host.OBSERVATION, host.STREAM,
                     *("scripts/" + name + ".py" for name in stream_lab.NAMES)):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.args = (cls.fixture.builder, "1" * 40, cls.fixture.bundle, cls.extra,
                    b"synthetic-validator", "local-correctness", "B300", "confidential", 2)
        cls.options = {"tokenizer_parity": True, "tokenizer_materialization": True,
                       "runtime_observation": True, "stream_cohort": True}
        cls.files, cls.contract = host.make_payload(*cls.args, **cls.options, model_profile="uncensored")

    def test_absent_and_explicit_original_payload_are_byte_identical_to_old_builder(self):
        old = types.ModuleType("original_host")
        old.__file__ = str(ROOT / host.SCRIPT)
        exec(compile(git(ROOT, "show", BASE + ":" + host.SCRIPT), old.__file__, "exec"), old.__dict__)
        # Same input bytes, including current reviewed runtime source: selection
        # must not silently add fields, helpers or a different default model.
        expected = old.make_payload(*self.args, **self.options)
        self.assertEqual(expected, host.make_payload(*self.args, **self.options))
        self.assertEqual(expected, host.make_payload(*self.args, **self.options, model_profile="original"))
        self.assertNotIn("model_profile", expected[1])
        self.assertNotIn("model-profile.py", expected[0])
        old_lab = types.ModuleType("original_lab")
        exec(compile(git(ROOT, "show", BASE + ":scripts/engine_lab.py"), "original_lab.py", "exec"), old_lab.__dict__)
        plan = self.fixture.builder.recorder.prep.launch_plan()
        for replicas in (1, 2):
            self.assertEqual(old_lab.lab_args(plan, replicas), lab.lab_args(plan, replicas))
        self.assertEqual(old_lab.lab_environment(plan), lab.lab_environment(plan))

    def test_opt_in_identity_sources_limits_and_selected_pack(self):
        old_files, old = host.make_payload(*self.args, **self.options)
        self.assertEqual(self.contract["model_profile"], NEW)
        self.assertEqual(self.contract["manifest_sha256"], NEW["manifest_sha256"])
        self.assertEqual(self.contract["pack_bytes"], NEW["total_bytes"])
        self.assertEqual(self.contract["pack_revision"], NEW["hf_revision"])
        self.assertEqual(self.files["pack/files.sha256"], (ROOT / NEW["pack"] / "files.sha256").read_bytes())
        self.assertEqual(self.files["pack/pack.yaml"], (ROOT / NEW["pack"] / "pack.yaml").read_bytes())
        self.assertEqual(self.contract["sources"]["model-profile.py"], host.MODEL_PROFILE_SHA)
        for key in ("base_image", "official_config", "application_contract_sha256", "candidate_bundle_sha256",
                    "paths", "environment", "interpreter", "tokenizer_materialization", "stream_cohort"):
            self.assertEqual(self.contract[key], old[key])
        args = list(old["arguments"])
        args[args.index("--served-model-name") + 1] = NEW["served_model_name"]
        self.assertEqual(args, self.contract["arguments"])
        self.assertEqual(lab.MODEL, profiles.select()["served_model_name"])
        self.assertLessEqual(len(self.contract["sources"]), 110)
        with payload_tree(self.files) as root:
            self.assertEqual(host.validate_model_profile(self.contract, root), NEW)
            command = ("name", "owner", root, "/weights")
            config = {"config": {"Env": []}}
            expected = host.create_command(*command, old, config)
            for flag in ("--memory", "--memory-swap"):
                expected[expected.index(flag) + 1] = "512g"
            self.assertEqual(host.create_command(*command, self.contract, config), expected)

    def test_memory_ceiling_changes_only_uncensored_docker_memory_arguments(self):
        old = types.ModuleType("previous_memory_host")
        old.__file__ = str(ROOT / host.SCRIPT)
        exec(compile(git(ROOT, "show", "fc55fd09efd1e52a2c1302281c7c634fefee2d04:" + host.SCRIPT),
                     old.__file__, "exec"), old.__dict__)
        # Compare memory behavior with the same reviewed helper bytes.
        old.MODEL_PROFILE_SHA = host.MODEL_PROFILE_SHA
        config = {"config": {"Env": ["NVIDIA_REQUIRE_CUDA=cuda>=13.0"]}}
        cases = [("functional", "B200", "confidential", 1, 512),
                 ("hardware-diagnostic", "B200", "confidential", 1, 512),
                 ("local-correctness", "B200", "confidential", 1, 512),
                 ("local-correctness", "B300", "confidential", 1, 512),
                 ("local-correctness", "B300", "nonconfidential", 1, 512),
                 ("local-correctness", "B300", "confidential", 2, 1024),
                 ("pre-serve", "B200", "confidential", 1, 8),
                 ("pre-serve", "B300", "confidential", 1, 8)]
        with payload_tree(self.files) as root:
            for operation, family, security, replicas, memory in cases:
                contract = {"base_image": "pinned", "environment": {}, "operation": operation,
                            "gpu_family": family, "gpu_security": security, "data_parallel_size": replicas}
                weights = None if operation == "pre-serve" else "/weights"
                args = ("owned", "owner", root, weights, contract, config)
                with self.subTest(profile=(operation, family, security, replicas)):
                    self.assertEqual(json.dumps(host.create_command(*args)).encode(),
                                     json.dumps(old.create_command(*args)).encode())
                    self.assertEqual(host.memory_gib(contract, root), memory)
            args = ("owned", "owner", root, "/weights", self.contract, config)
            expected = old.create_command(*args)
            for flag in ("--memory", "--memory-swap"):
                self.assertEqual(expected[expected.index(flag) + 1], "1024g")
                expected[expected.index(flag) + 1] = "512g"
            self.assertEqual(host.create_command(*args), expected)
            self.assertEqual(host.memory_gib(self.contract, root), 512)

    def test_uncensored_actual_container_memory_and_swap_must_equal_512_gib(self):
        config = {"config": {"Env": []}}
        with payload_tree(self.files) as root:
            args = host.create_command("owned", "owner", root, "/weights", self.contract, config)
            info = {
                "Image": "image-id",
                "Config": {"Image": self.contract["base_image"], "Labels": {host.LABEL: "owner"},
                           "Entrypoint": ["/usr/bin/env"], "Cmd": args[args.index(self.contract["base_image"]) + 1:],
                           "User": "0:0", "WorkingDir": "/", "Healthcheck": {"Test": ["NONE"]},
                           "Env": [args[i + 1] for i, arg in enumerate(args) if arg == "--env"]},
                "HostConfig": {"NetworkMode": "none", "Privileged": False, "Runtime": "nvidia",
                               "ReadonlyRootfs": False, "CapDrop": ["ALL"], "IpcMode": "private",
                               "ShmSize": 2 << 30, "CgroupnsMode": "private", "Memory": 512 << 30,
                               "MemorySwap": 512 << 30, "NanoCpus": 64000000000, "PidsLimit": 1024,
                               "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
                               "SecurityOpt": ["no-new-privileges"],
                               "Tmpfs": {"/cache": "rw,nosuid,nodev,exec,size=16g,mode=0700",
                                         "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"},
                               "DeviceRequests": [{"DeviceIDs": list(map(str, range(8))),
                                                   "Capabilities": [["gpu"]], "Count": 0, "Driver": "nvidia"}]},
                "Mounts": [{"Type": "bind", "Source": str(root / "payload"), "Destination": "/opt/nib", "RW": False},
                           {"Type": "bind", "Source": "/weights", "Destination": "/model", "RW": False}],
            }
            host.check_container(info, "owned", "owner", root, "/weights", self.contract, config, "image-id")
            for keys in (("Memory",), ("MemorySwap",), ("Memory", "MemorySwap")):
                for value in (0, -1, 8 << 30, 511 << 30, 768 << 30, 1024 << 30, "512g", True):
                    changed = copy.deepcopy(info)
                    for key in keys:
                        changed["HostConfig"][key] = value
                    with self.subTest(keys=keys, value=value), self.assertRaises(ValueError):
                        host.check_container(changed, "owned", "owner", root, "/weights", self.contract, config, "image-id")

    def test_memory_helper_rejects_unbound_or_wrong_variant_identity(self):
        with payload_tree(self.files) as root:
            changes = ({"model_profile": None}, {"model_profile": profiles.select()},
                       {"model_profile": {**NEW, "hf_revision": "0" * 40}},
                       {"manifest_sha256": "0" * 64}, {"pack_bytes": NEW["total_bytes"] - 1},
                       {"operation": "pre-serve"}, {"gpu_family": "H200"},
                       {"gpu_security": "auto"}, {"data_parallel_size": 1})
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    host.memory_gib({**self.contract, **change}, root)
            (root / "payload/model-profile.py").write_bytes(self.files["model-profile.py"] + b"\n")
            with self.assertRaises(ValueError):
                host.memory_gib(self.contract, root)

    def test_uncensored_cgroup_observation_requires_same_cap_and_zero_swap(self):
        row = observation_fixtures.sample()
        row["exe_sha256"] = self.contract["interpreter"]["sha256"]
        observation.validate_sample(row, self.contract)
        self.assertEqual(row["cgroup"]["memory.max"], host.ENGINE_MEMORY_GIB << 30)
        for name, value in (("memory.max", 0), ("memory.max", -1), ("memory.max", 511 << 30),
                            ("memory.max", 768 << 30), ("memory.max", 1024 << 30),
                            ("memory.max", "max"), ("memory.swap.max", 1),
                            ("memory.peak", (512 << 30) + 1)):
            changed = copy.deepcopy(row)
            changed["cgroup"][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                observation.validate_sample(changed, self.contract)
        for change in ({"model_profile": None}, {"model_profile": profiles.select()},
                       {"gpu_security": "auto"}, {"gpu_family": "H200"},
                       {"data_parallel_size": 1}, {"operation": "pre-serve"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                observation.validate_sample(row, {**self.contract, **change})
    def test_mixed_identity_manifest_source_or_runtime_rejected(self):
        plan = self.fixture.builder.recorder.prep.launch_plan()
        def read_source(path, *_):
            return self.files[path.removeprefix("/opt/nib/")]
        with payload_tree(self.files) as root, mock.patch.object(lab, "read_source", side_effect=read_source):
            self.assertEqual(lab.validate_settings(self.contract, plan, 2), NEW)
            changes = [{"pack_revision": profiles.select()["hf_revision"]}, {"pack_bytes": profiles.select()["total_bytes"]},
                       {"manifest_sha256": profiles.select()["manifest_sha256"]}, {"model_profile": profiles.select()},
                       {"model_profile": None}, {"data_parallel_size": 1}, {"gpu_security": "auto"},
                       {"operation": "pre-serve"}, {"provider_conformance": {}}]
            for key in NEW:
                identity = {**NEW, key: "changed" if type(NEW[key]) is str else NEW[key] + 1}
                changes.append({"model_profile": identity})
            for changed in changes:
                value = {**self.contract, **changed}
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    host.validate_model_profile(value, root)
                with self.assertRaises(ValueError):
                    lab.validate_settings(value, plan, 2)
            for flag, wrong in (("--served-model-name", lab.MODEL), ("--max-model-len", "16384"),
                                ("--data-parallel-size", "1"), ("--max-num-seqs", "2")):
                args = list(self.contract["arguments"])
                args[args.index(flag) + 1] = wrong
                with self.subTest(flag=flag), self.assertRaises(ValueError):
                    lab.validate_settings({**self.contract, "arguments": args}, plan, 2)
            missing = copy.deepcopy(self.contract)
            del missing["model_profile"]
            with self.assertRaises(ValueError):
                lab.validate_settings(missing, plan, 2)
            with self.assertRaises(ValueError):
                host.validate_model_profile(missing, root)
            (root / "payload/model-profile.py").write_bytes(self.files["model-profile.py"] + b"\n")
            with self.assertRaises(ValueError):
                host.validate_model_profile(self.contract, root)
        changed = {**self.extra, NEW["pack"] + "/files.sha256": old_manifest()}
        with self.assertRaises(ValueError):
            host.make_payload(*(*self.args[:3], changed, *self.args[4:]), model_profile="uncensored")

    def test_new_provider_and_other_modes_rejected_before_preparation(self):
        for options in ({"provider_conformance": {}}, {"model_profile": None}):
            with self.assertRaises(ValueError):
                host.make_payload(*self.args, **{**self.options, "model_profile": "uncensored", **options})
        for operation, family, security, replicas in (("functional", "B200", "confidential", 1),
                ("pre-serve", "B300", "confidential", 1), ("local-correctness", "B300", "confidential", 1),
                ("local-correctness", "B200", "nonconfidential", 2)):
            with mock.patch.object(host, "load") as load, self.assertRaises(ValueError):
                host.prepare("/unused", operation, family, security, replicas, model_profile="uncensored")
            load.assert_not_called()

    def test_cli_selection_is_prepare_only_and_explicit_original_is_unchanged(self):
        command = [host.SCRIPT, "--prepare-local-correctness-b300-dp2", "/unused"]
        for name in (None, "original", "uncensored"):
            argv = command + (["--model-profile", name] if name else [])
            with mock.patch.object(sys, "argv", argv), mock.patch.object(host, "prepare") as prepare:
                self.assertEqual(host.main(), 0)
            self.assertEqual(prepare.call_args.kwargs, {"model_profile": "uncensored"} if name == "uncensored" else {})
        for argv in ([host.SCRIPT, "--run", "/unused", "--model-profile", "original"],
                     command + ["--model-profile", "unknown"],
                     command + ["--model-profile", "uncensored", "--provider-conformance-wheels", "/unused"],
                     [host.SCRIPT, "--prepare", "/unused", "--model-profile", "uncensored"]):
            with mock.patch.object(sys, "argv", argv), mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_new_result_requires_same_identity_and_old_receipt_is_not_relabelled(self):
        row = {"schema": "nib.deepseek-lab-tokenizer-parity/v1", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": False, "stage": "hardware", "application": None,
               "hardware": None, "completion": None, "startup": None, "engine_stopped": False,
               "tokenizer_parity": None, "model_profile": NEW}
        files, contract = host.make_payload(*self.args, model_profile="uncensored", tokenizer_parity=True)
        with payload_tree(files) as root:
            host.validate_result(row, root, contract, None)
            for wrong in (None, profiles.select()):
                with self.assertRaises(ValueError):
                    host.validate_result({**row, "model_profile": wrong}, root, contract, None)
            del row["model_profile"]
            with self.assertRaises(ValueError):
                host.validate_result(row, root, contract, None)

    def test_runtime_failure_preserves_identity_and_denial_only_status(self):
        files, contract = host.make_payload(*self.args, model_profile="uncensored")
        with mock.patch.object(lab, "read_source", side_effect=lambda path, *_: files[path.removeprefix("/opt/nib/")]), \
                mock.patch.object(lab, "check_scratch_mounts", side_effect=ValueError("synthetic-failure")):
            row = lab.application_and_request(contract, files, [None] * 4, {}, {})
        self.assertEqual(row["model_profile"], NEW)
        self.assertEqual(row["status"], "BLOCKED")
        for key in ("functional_pass", "deployable", "confidential", "engine_stopped"):
            self.assertIs(row[key], False)
        self.assertNotIn("synthetic-failure", json.dumps(row))
        with payload_tree(files) as root:
            host.validate_result(row, root, contract, None)
            for key in ("functional_pass", "deployable", "confidential"):
                with self.assertRaises(ValueError):
                    host.validate_result({**row, key: True}, root, contract, None)


def old_manifest():
    return (ROOT / host.PACK / "files.sha256").read_bytes()


class B200Profiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PayloadProfiles.setUpClass()
        cls.fixture = PayloadProfiles.fixture
        cls.extra = PayloadProfiles.extra
        cls.args = (*PayloadProfiles.args[:6], "B200", "confidential", 2)
        cls.options = {**PayloadProfiles.options, "stream_profile": stream_lab.EXTENDED_PROFILE}
        cls.files, cls.contract = host.make_payload(*cls.args, **cls.options, model_profile="uncensored")

    def test_only_new_family_changes_b300_contract_and_fixed_settings(self):
        files, contract = host.make_payload(*PayloadProfiles.args, **self.options, model_profile="uncensored")
        self.assertEqual(self.contract, {**contract, "gpu_family": "B200"})
        for name in files.keys() - {"lab.json", "python"}:
            self.assertEqual(files[name], self.files[name])
        self.assertEqual(self.contract["model_profile"], NEW)
        self.assertEqual(self.contract["sources"]["model-profile.py"], host.MODEL_PROFILE_SHA)
        with payload_tree(self.files) as root:
            old = host.create_command("owned", "owner", root, "/weights", contract, {"config": {"Env": []}})
            new = host.create_command("owned", "owner", root, "/weights", self.contract, {"config": {"Env": []}})
            self.assertEqual(old, new)
            for flag, value in (("--memory", "512g"), ("--memory-swap", "512g"), ("--cpus", "64"),
                                ("--pids-limit", "1024"), ("--network", "none"),
                                ("--gpus", '\"device=0,1,2,3,4,5,6,7\"')):
                self.assertEqual(new[new.index(flag) + 1], value)
        args = self.contract["arguments"]
        for flag, value in (("--tensor-parallel-size", "4"), ("--data-parallel-size", "2"),
                            ("--max-model-len", "4096"), ("--max-num-seqs", "1"),
                            ("--engram-config", '{"cpu_offload":false}')):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--enforce-eager", args)
        self.assertEqual((host.MAX_SECONDS, lab.WEIGHTS_SECONDS, lab.APPLICATION_SECONDS, lab.STARTUP_SECONDS),
                         (4800, 1800, 720, 1800))

    def test_all_legacy_payloads_and_docker_commands_match_frozen_base(self):
        previous = types.ModuleType("previous_b200_host")
        previous.__file__ = str(ROOT / host.SCRIPT)
        exec(compile(git(ROOT, "show", "f9885c6405bdc8675dacf3c04ecd583223d0dfbd:" + host.SCRIPT),
                     previous.__file__, "exec"), previous.__dict__)
        # Compare unchanged builder behavior with the same reviewed helper bytes.
        # Selected checker tests separately prove the helper's new opt-in scope.
        previous.MODEL_PROFILE_SHA = host.MODEL_PROFILE_SHA
        cases = [("functional", "B200", "confidential", 1), ("hardware-diagnostic", "B200", "confidential", 1),
                 ("local-correctness", "B200", "confidential", 1), ("local-correctness", "B200", "nonconfidential", 1),
                 ("local-correctness", "B300", "confidential", 1), ("local-correctness", "B300", "nonconfidential", 1),
                 ("local-correctness", "B300", "confidential", 2), ("pre-serve", "B200", "confidential", 1),
                 ("pre-serve", "B300", "confidential", 1)]
        for profile in cases:
            for identity in ("original", "uncensored") if profile == cases[6] else ("original",):
                options = {"model_profile": identity}
                if profile[0] == "local-correctness" and profile[1:3] == ("B300", "confidential"):
                    options.update(self.options)
                args = (*self.args[:5], *profile)
                with self.subTest(profile=profile, identity=identity):
                    expected = previous.make_payload(*args, **options)
                    self.assertEqual(host.make_payload(*args, **options), expected)
                    files, contract = expected
                    with payload_tree(files) as root:
                        command = ("owned", "owner", root, None if profile[0] == "pre-serve" else "/weights",
                                   contract, {"config": {"Env": []}})
                        self.assertEqual(json.dumps(host.create_command(*command)).encode(),
                                         json.dumps(previous.create_command(*command)).encode())

    def test_cli_and_preparation_reject_implicit_or_wrong_b200_selection(self):
        command = [host.SCRIPT, "--prepare-local-correctness-b200-dp2", "/unused"]
        for suffix in ([], ["--model-profile", "original"], ["--model-profile", "unknown"],
                       ["--model-profile", "uncensored", "--provider-conformance-wheels", "/unused"],
                       ["--model-profile", "uncensored", "--prepare", "/other"]):
            with mock.patch.object(sys, "argv", command + suffix), mock.patch.object(host, "prepare") as prepare, \
                    self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()
        for identity in ("original", None, "unknown"):
            with mock.patch.object(host, "load") as load, self.assertRaises(ValueError):
                host.prepare("/unused", "local-correctness", "B200", "confidential", 2, model_profile=identity)
            load.assert_not_called()

    def test_identity_validation_precedes_any_runtime_action(self):
        plan = self.fixture.builder.recorder.prep.launch_plan()
        changes = [{"gpu_family": None}, {"gpu_family": "b200"}, {"gpu_family": "H200"},
                   {"model_profile": None}, {"model_profile": profiles.select()},
                   {"gpu_security": "nonconfidential"}, {"data_parallel_size": 1}, {"data_parallel_size": 2.0},
                   {"pack_revision": profiles.select()["hf_revision"]}, {"pack_bytes": NEW["total_bytes"] - 1},
                   {"manifest_sha256": "0" * 64}, {"operation": "functional"}, {"provider_conformance": {}}]
        changes += [{"model_profile": {**NEW, key: "wrong" if type(value) is str else value + 1}}
                    for key, value in NEW.items()]
        with payload_tree(self.files) as root, mock.patch.object(lab, "read_source", side_effect=
                lambda path, *_: self.files[path.removeprefix("/opt/nib/")]):
            self.assertEqual(lab.validate_settings(self.contract, plan, 2), NEW)
            for change in changes:
                bad = {**self.contract, **change}
                with self.subTest(change=change), self.assertRaises(ValueError):
                    host.memory_gib(bad, root)
                with mock.patch.object(lab, "check_scratch_mounts") as scratch, self.assertRaises(ValueError):
                    lab.application_and_request(bad, self.files, [None] * 4, {}, {})
                scratch.assert_not_called()
            for missing in ("model_profile", "gpu_family", "data_parallel_size"):
                bad = {key: value for key, value in self.contract.items() if key != missing}
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    host.validate_model_profile(bad, root)
                with self.assertRaises(ValueError):
                    lab.validate_settings(bad, plan, 2)
            (root / "payload/model-profile.py").write_bytes(self.files["model-profile.py"] + b"\n")
            with self.assertRaises(ValueError):
                host.memory_gib(self.contract, root)

    def test_optional_gates_do_not_open_original_b200_or_other_profiles(self):
        for validate in (host.parallel_size, fixtures.probe.parallel_size, parity.enabled,
                         observation.enabled, stream_lab.enabled):
            validate(self.contract)
            for change in ({"model_profile": None}, {"model_profile": profiles.select()},
                           {"gpu_security": "nonconfidential"}, {"operation": "pre-serve"},
                           {"gpu_family": "H200"}, {"data_parallel_size": 2.0}):
                with self.subTest(check=validate.__name__, change=change), self.assertRaises(ValueError):
                    validate({**self.contract, **change})

    def test_eight_actual_devices_keep_b200_and_reject_mixed_or_unready(self):
        probe = fixtures.probe
        library = fixtures.DiagnosticTests().library(count=8, name=b"NVIDIA B200", memory=160 << 20)
        with mock.patch.object(probe.c, "CDLL", return_value=library):
            raw = probe.NVML().observe("B200", expected=8)
        row = probe.validate(raw, family="B200", expected=8)
        self.assertEqual(probe.validate_report(row, family="B200", expected=8), row)
        self.assertEqual(row["family"], "B200")
        self.assertEqual(row["selected_gpus"], 8)
        self.assertIs(row["attestation_verified"], False)
        for index in range(8):
            for device in ({"b300": True, "protected_kib": 250 << 20},
                           {"b200": False, "protected_kib": 160 << 20},
                           {"b200": True, "protected_kib": (120 << 20) - 1}):
                bad = copy.deepcopy(raw)
                bad["devices"][index] = device
                with self.subTest(index=index, device=device), self.assertRaises(ValueError):
                    probe.validate(bad, family="B200", expected=8)
        for change in ({"devices": raw["devices"][:4]}, {"ready": 0}, {"feature": 0}, {"devtools": 1},
                       {"environment": 1}, {"multigpu": 0}, {"cuda_driver": 12080}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                probe.validate({**raw, **change}, family="B200", expected=8)
        with self.assertRaises(ValueError):
            probe.validate_report(row, family="B300", expected=8)

    def test_actual_container_and_cgroup_bounds_stay_512_gib(self):
        # Reuse the complete actual-inspect fixture and all cap/swap negatives
        # with this B200 payload, then mutate the other owned resources.
        real_check, seen = host.check_container, []
        def check(info, *args):
            real_check(info, *args)
            seen.append((copy.deepcopy(info), args))
        with mock.patch.object(host, "check_container", side_effect=check):
            PayloadProfiles.test_uncensored_actual_container_memory_and_swap_must_equal_512_gib(self)
        info, args = seen[0]
        with payload_tree(self.files) as root:
            args = (*args[:2], root, *args[3:])
            info["Mounts"][0]["Source"] = str(root / "payload")
            real_check(info, *args)
            for key, value in (("NanoCpus", 240000000000), ("PidsLimit", 2048), ("ShmSize", 4 << 30),
                               ("NetworkMode", "host"), ("Privileged", True),
                               ("DeviceRequests", [{"DeviceIDs": list(map(str, range(4))),
                                 "Capabilities": [["gpu"]], "Count": 0, "Driver": "nvidia"}])):
                bad = copy.deepcopy(info)
                bad["HostConfig"][key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    real_check(bad, *args)
        PayloadProfiles.test_uncensored_cgroup_observation_requires_same_cap_and_zero_swap(self)

    def test_cli_to_published_bundle_to_runtime_failure_retains_binding(self):
        # Only Git/build/OCI inputs are synthetic. Exercise the actual CLI,
        # preparer, publisher, bundle reader and runtime identity/result checks.
        real_load, real_read = host.load, host.read
        config = {"config": {"Env": ["LD_LIBRARY_PATH=" + lab.IMAGE_LD_PATH, "CUDA_HOME=/usr/local/cuda"]}}
        def build(_root, _revision, work, _env):
            binary = work / "synthetic-validator"
            binary.write_bytes(b"synthetic-linux-validator-not-executed")
            return binary, "a" * 64
        def load(name, path, sha):
            module = real_load(name, path, sha)
            if name == "lab_application_builder":
                module.inputs = lambda _: ("1" * 40, dict(self.fixture.bundle), dict(self.extra))
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
            command = [host.SCRIPT, "--prepare-local-correctness-b200-dp2", str(root),
                       "--model-profile", "uncensored", "--tokenizer-parity", "--tokenizer-materialization",
                       "--runtime-observation", "--stream-cohort", "--stream-profile", stream_lab.EXTENDED_PROFILE]
            stack.enter_context(mock.patch.object(sys, "argv", command))
            stack.enter_context(mock.patch.object(host, "load", side_effect=load))
            stack.enter_context(mock.patch.object(host, "read", side_effect=read))
            stack.enter_context(mock.patch.object(host, "committed", side_effect=lambda _b, _r, name: (ROOT / name).read_bytes()))
            stack.enter_context(mock.patch("builtins.print"))
            self.assertEqual(host.main(), 0)
            receipt_sha = host.digest(real_read(root / "receipt.json"))
            receipt, contract, _, _ = host.bundle_inputs(root, receipt_sha)
            self.assertEqual(contract["gpu_family"], "B200")
            self.assertEqual(contract["model_profile"], NEW)
            self.assertEqual(receipt["source_bundle_sha256"], contract["source_bundle_sha256"])
            sources = {name: real_read(root / "payload" / name) for name in contract["sources"]}
            plan = json.loads(real_read(root / "payload/launch-plan.json"))
            stack.enter_context(mock.patch.object(lab, "read_source", side_effect=
                lambda path, *_: real_read(root / "payload" / path.removeprefix("/opt/nib/"))))
            self.assertEqual(lab.validate_settings(contract, plan, 2), NEW)
            stack.enter_context(mock.patch.object(lab, "tokenizer_adapter_options", fixtures.bootstrap.tokenizer_adapter_options, create=True))
            stack.enter_context(mock.patch.object(lab, "STREAM_LAB", stream_lab))
            stack.enter_context(mock.patch.object(lab, "RUNTIME_OBSERVER", observation))
            stack.enter_context(mock.patch.object(lab, "GPU_PROBE", fixtures.probe))
            stack.enter_context(mock.patch.object(lab, "check_scratch_mounts"))
            stack.enter_context(mock.patch.object(lab, "hardware", side_effect=ValueError("synthetic-private-rejection")))
            row = lab.application_and_request(contract, sources, [None] * 4, {}, {})
            host.validate_result(row, root, contract, None)
            self.assertEqual(row["model_profile"], NEW)
            self.assertEqual(row["status"], "BLOCKED")
            for name in ("functional_pass", "deployable", "confidential"):
                self.assertIs(row[name], False)
            self.assertNotIn("synthetic-private-rejection", json.dumps(row))
            for name in ("probe.py", "model-profile.py", "lab.json"):
                path = root / "payload" / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.subTest(name=name), self.assertRaises(ValueError):
                    host.bundle_inputs(root, receipt_sha)
                path.write_bytes(original)


class RuntimeProfiles(unittest.TestCase):
    def test_four_parity_probes_use_new_identity_and_reject_old_completion(self):
        for reply_model in (NEW["served_model_name"], lab.MODEL):
            seen = []
            def request(path, payload, deadline):
                seen.append(payload["model"])
                value = json.loads(parity_fixtures.response(path, payload, deadline))
                if path != "/tokenize":
                    value["model"] = reply_model
                return parity.encoded(value)
            row = parity.run(request, parity_fixtures.local(), NEW["served_model_name"], correctness,
                             deadline=time.monotonic() + 5)
            self.assertEqual(row["all_cases_pass"], reply_model == NEW["served_model_name"])
            self.assertEqual(row["requests_started"], 8 if row["all_cases_pass"] else 2)
            self.assertEqual(set(seen), {NEW["served_model_name"]})
            for key in ("quality_accepted", "confidential", "tau_acceptance"):
                self.assertIs(row[key], False)
            self.assertNotIn(parity_fixtures.CANARY, json.dumps(row))

    def test_completion_model_must_match_selected_identity(self):
        response = {"object": "chat.completion", "model": NEW["served_model_name"],
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "2"}}],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}}
        self.assertTrue(lab.completion_result(json.dumps(response), model=NEW["served_model_name"])["synthetic_answer_match"])
        for name in (lab.MODEL, "unknown", None):
            with self.assertRaises(ValueError):
                lab.completion_result(json.dumps({**response, "model": name}), model=NEW["served_model_name"])
        with self.assertRaises(ValueError):
            lab.completion_result(json.dumps(response))

    def test_readiness_requires_selected_model_not_original_or_alias(self):
        for name in (NEW["served_model_name"], lab.MODEL, "unknown"):
            observation = {"outcome": "launch-failed", "wait_ms": 0, "exit_code": None, "health_probes": 0}
            engine = mock.Mock()
            engine.poll.return_value = None
            with mock.patch.object(lab, "http_request", side_effect=[b"", json.dumps({"data": [{"id": name}]}).encode()]):
                if name == NEW["served_model_name"]:
                    lab.observe_startup(engine, observation, model=name)
                    self.assertEqual(observation["outcome"], "ready")
                else:
                    with self.assertRaises(ValueError):
                        lab.observe_startup(engine, observation, model=NEW["served_model_name"])
                    self.assertEqual(observation["outcome"], "models-rejected")

    def test_stream_real_socket_uses_selected_name_and_rejects_original_response(self):
        for returned in (NEW["served_model_name"], stream_transport.MODEL):
            raw = b"".join(pieces()).replace(stream_transport.MODEL.encode(), returned.encode())
            with server(raw) as seen, mock.patch.object(stream_transport.identity.Endpoint, "check"):
                client = stream_transport.OwnedStreamHTTP(endpoint(), model=NEW["served_model_name"])
                result = collect(client)
            self.assertEqual(seen[0][2]["model"], NEW["served_model_name"])
            self.assertEqual(result.status, "completed" if returned == NEW["served_model_name"] else "protocol-error")
            self.assertNotIn("private-synthetic-stream-canary", json.dumps(result.metadata()))
        self.assertEqual(stream_transport.MODEL, profiles.select()["served_model_name"])

    def test_cohort_identity_is_explicit_and_does_not_mutate_shared_module(self):
        import test_stream_lab as fixture
        contract = {**fixture.contract(), "model_profile": NEW}
        with fake_collection([sample()] * 9):
            cohort = stream_cohort.run(endpoint(), workload(), deadline=120, gpu_count=8, model=NEW["served_model_name"])
        value = {"schema": stream_lab.RESULT_SCHEMA, "contract_sha256": "a" * 64,
                 "engine_identity_sha256": "b" * 64, "terminal_identity_verified": True, "cohort": cohort}
        self.assertEqual(cohort["model"], NEW["served_model_name"])
        self.assertTrue(stream_lab.validate(value, contract, "a" * 64, stream_cohort, model=NEW["served_model_name"]))
        for name in (None, stream_transport.MODEL):
            with self.assertRaises(ValueError):
                stream_lab.validate(value, contract, "a" * 64, stream_cohort, model=name)
        cohort["model"] = stream_transport.MODEL
        with self.assertRaises(ValueError):
            stream_lab.validate(value, contract, "a" * 64, stream_cohort, model=NEW["served_model_name"])
        self.assertEqual(stream_cohort.stream.MODEL, profiles.select()["served_model_name"])


class OrdinaryUncensored(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        PayloadProfiles.setUpClass()
        cls.args = (*PayloadProfiles.args[:7], "nonconfidential", 2)
        cls.files, cls.contract = host.make_payload(*cls.args, model_profile="uncensored")

    def test_explicit_eight_gpu_profile_keeps_fixed_checkpoint_and_limits(self):
        cc_files, cc = host.make_payload(*PayloadProfiles.args, model_profile="uncensored")
        self.assertEqual(self.contract, {**cc, "gpu_security": "nonconfidential"})
        self.assertEqual(self.files["pack/files.sha256"], cc_files["pack/files.sha256"])
        for key in ("confidential", "deployable"):
            self.assertIs(self.contract[key], False)
        self.assertEqual(host.parallel_size(self.contract), 2)
        self.assertEqual(fixtures.probe.parallel_size(self.contract), 2)
        plan = PayloadProfiles.fixture.builder.recorder.prep.launch_plan()
        with payload_tree(self.files) as root, mock.patch.object(lab, "read_source", side_effect=
                lambda path, *_: self.files[path.removeprefix("/opt/nib/")]):
            self.assertEqual(host.validate_model_profile(self.contract, root), NEW)
            self.assertEqual(lab.validate_settings(self.contract, plan, 2), NEW)
            self.assertEqual(host.memory_gib(self.contract, root), 512)
        for key in ("tokenizer_parity", "runtime_observation", "stream_cohort", "provider_conformance"):
            self.assertNotIn(key, self.contract)

    def test_nonconfidential_cannot_enable_unreviewed_modes(self):
        for options in ({"tokenizer_materialization": True},
                        {"stream_cohort": True}, {"provider_conformance": {}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                host.make_payload(*self.args, model_profile="uncensored", **options)
        for change in ({"gpu_family": "B200"}, {"gpu_security": "auto"},
                       {"gpu_security": None}, {"data_parallel_size": 1},
                       {"model_profile": profiles.select()}, {"operation": "pre-serve"}):
            with payload_tree(self.files) as root, self.subTest(change=change), self.assertRaises(ValueError):
                host.validate_model_profile({**self.contract, **change}, root)

    def test_cli_is_explicit_and_default_modes_stay_closed(self):
        command = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused"]
        with mock.patch.object(sys, "argv", command + ["--model-profile", "uncensored"]), \
                mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                                            model_profile="uncensored")
        for flags in ([], ["--model-profile", "original"],
                      ["--model-profile", "uncensored", "--stream-cohort"],
                      ["--model-profile", "uncensored", "--provider-conformance-wheels", "/unused"]):
            with mock.patch.object(sys, "argv", command + flags), mock.patch.object(host, "prepare") as prepare, \
                    self.subTest(flags=flags), self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_explicit_measurements_reuse_cc_harness_without_cc_claims(self):
        options = {"tokenizer_parity": True, "tokenizer_materialization": True,
                   "runtime_observation": True, "stream_cohort": True}
        cc_files, cc = host.make_payload(*PayloadProfiles.args, model_profile="uncensored", **options)
        files, contract = host.make_payload(*self.args, model_profile="uncensored", **options)
        self.assertEqual(contract, {**cc, "gpu_security": "nonconfidential"})
        for name in files.keys() - {"lab.json", "python"}:
            self.assertEqual(files[name], cc_files[name])
        self.assertNotEqual(files["python"], cc_files["python"])
        for key in ("confidential", "deployable"):
            self.assertIs(contract[key], False)
        plan = PayloadProfiles.fixture.builder.recorder.prep.launch_plan()
        with payload_tree(files) as root, mock.patch.object(lab, "read_source", side_effect=
                lambda path, *_: files[path.removeprefix("/opt/nib/")]):
            self.assertEqual(host.validate_model_profile(contract, root), NEW)
            self.assertEqual(lab.validate_settings(contract, plan, 2), NEW)
            command = host.create_command("owned", "owner", root, "/weights", contract, {"config": {"Env": []}})
            self.assertEqual(command[command.index("--network") + 1], "none")
            self.assertEqual(command[command.index("--memory") + 1], "512g")
            self.assertEqual(command[command.index("--memory-swap") + 1], "512g")
        stream_lab.load(files, contract["sources"])
        for check in (parity.enabled, observation.enabled, stream_lab.enabled):
            check(contract)
            for change in ({"gpu_family": "B200"}, {"gpu_family": "H200"}, {"gpu_security": "auto"},
                           {"model_profile": None}, {"model_profile": profiles.select()},
                           {"data_parallel_size": 1}, {"data_parallel_size": True}, {"data_parallel_size": 2.0},
                           {"operation": "functional"}, {"provider_conformance": {}}):
                with self.subTest(check=check.__module__, change=change), self.assertRaises(ValueError):
                    check({**contract, **change})
        sample = observation_fixtures.sample()
        sample["exe_sha256"] = contract["interpreter"]["sha256"]
        observation.validate_sample(sample, contract)
        sample["cgroup"]["memory.swap.max"] = 1
        with self.assertRaises(ValueError):
            observation.validate_sample(sample, contract)

    def test_measurement_cli_requires_explicit_model_and_ordinary_profile(self):
        command = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                   "--model-profile", "uncensored", "--tokenizer-parity", "--tokenizer-materialization",
                   "--runtime-observation", "--stream-cohort", "--stream-profile", stream_lab.EXTENDED_PROFILE]
        with mock.patch.object(sys, "argv", command), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            prepare.assert_called_once_with("/unused", "local-correctness", "B300", "nonconfidential", 2,
                model_profile="uncensored", tokenizer_parity=True, tokenizer_materialization=True,
                runtime_observation=True, stream_cohort=True, stream_profile=stream_lab.EXTENDED_PROFILE)

    def test_all_eight_ordinary_devices_are_checked_and_never_attested(self):
        probe = fixtures.probe
        library = fixtures.DiagnosticTests().library(count=8, name=b"NVIDIA B300", memory=250 << 20,
                                                    feature=0, environment=0, multigpu=0)
        library.nvmlSystemGetConfComputeGpusReadyState = mock.Mock(side_effect=AssertionError("readiness"))
        library.nvmlDeviceGetConfComputeMemSizeInfo = mock.Mock(side_effect=AssertionError("protected"))
        with mock.patch.object(probe.c, "CDLL", return_value=library):
            raw = probe.NVML().observe("B300", nonconfidential=True, expected=8)
        row = probe.validate_nonconfidential(raw, family="B300", expected=8)
        self.assertEqual(row["selected_gpus"], 8)
        self.assertIs(row["attestation_verified"], False)
        library.nvmlSystemGetConfComputeGpusReadyState.assert_not_called()
        library.nvmlDeviceGetConfComputeMemSizeInfo.assert_not_called()
        with mock.patch.object(lab, "GPU_PROBE", probe, create=True), \
                mock.patch.object(lab, "quiet_command", return_value=json.dumps(row).encode()):
            self.assertEqual(lab.hardware("B300", "nonconfidential", 8), row)
        for expected in (4, 7, True, "8", None):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                probe.validate_nonconfidential_report(row, family="B300", expected=expected)
        for index in range(8):
            changed = copy.deepcopy(raw)
            changed["devices"][index]["total_kib"] = 199 << 20
            with self.subTest(index=index), self.assertRaises(ValueError):
                probe.validate_nonconfidential(changed, family="B300", expected=8)
        for validate, value in ((probe.validate, raw), (probe.validate_report, row)):
            with self.assertRaises(ValueError):
                validate(value, family="B300", expected=8)
        with self.assertRaises(ValueError):
            probe.validate_nonconfidential_report({**row, "attestation_verified": True}, family="B300", expected=8)
        for family in ("B200", "H200"):
            with self.assertRaises(ValueError):
                probe.validate_nonconfidential(raw, family=family, expected=8)


if __name__ == "__main__":
    unittest.main()
