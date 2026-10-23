# SPDX-License-Identifier: Apache-2.0
"""Fixed model selection and synthetic runtime boundaries, never GPU evidence."""

import copy
from contextlib import contextmanager
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
import test_engine_tokenizer_parity as parity_fixtures
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
            self.assertEqual(host.create_command(*command, self.contract, config), host.create_command(*command, old, config))

    def test_mixed_identity_manifest_source_or_runtime_rejected(self):
        plan = self.fixture.builder.recorder.prep.launch_plan()
        def read_source(path, *_):
            return self.files[path.removeprefix("/opt/nib/")]
        with payload_tree(self.files) as root, mock.patch.object(lab, "read_source", side_effect=read_source):
            self.assertEqual(lab.validate_settings(self.contract, plan, 2), NEW)
            changes = [{"pack_revision": profiles.select()["hf_revision"]}, {"pack_bytes": profiles.select()["total_bytes"]},
                       {"manifest_sha256": profiles.select()["manifest_sha256"]}, {"model_profile": profiles.select()},
                       {"model_profile": None}, {"data_parallel_size": 1}, {"gpu_security": "nonconfidential"},
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
                ("local-correctness", "B300", "nonconfidential", 2)):
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


if __name__ == "__main__":
    unittest.main()
