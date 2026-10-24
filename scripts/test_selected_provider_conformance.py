# SPDX-License-Identifier: Apache-2.0
"""Selected-checkpoint checker binding; synthetic tests are not GPU evidence."""

import hashlib
import sys
import unittest
from unittest import mock

import deepseek_model_profile as profiles
import provider_conformance as prep
import provider_conformance_session as session
import provider_conformance_worker as worker
import test_deepseek_model_profile as models
import test_provider_conformance as conformance
from test_provider_conformance_session import contract as original_contract

payload_tree, host, lab, ROOT = models.payload_tree, models.host, models.lab, models.ROOT
SELECTED = profiles.select("uncensored")


class WorkerIdentity(unittest.TestCase):
    setUp = conformance.ConformanceTests.setUp
    checks = conformance.ConformanceTests.checks

    def test_selected_pass_does_not_accept_original_or_other_model(self):
        run = self.checks()
        run.selected_model = SELECTED["served_model_name"]
        run.checks[0].measured["model_ids"] = [run.selected_model]
        self.assertFalse(worker.project(run, True)["native_identity_matches"])
        self.assertTrue(worker.project(run, True, model=run.selected_model)["mandatory_checks_passed"])
        self.assertFalse(worker.project(self.checks(), True, model=run.selected_model)["mandatory_checks_passed"])
        self.assertNotIn("PRIVATE_CONTENT_CANARY", str(worker.project(run, True, model=run.selected_model)))

    def test_selected_payload_is_source_pinned_not_just_self_consistent(self):
        source = (ROOT / prep.PROFILE_SOURCE).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), worker.PROFILE_SHA)
        self.assertEqual(worker.PROFILE_SHA, prep.PROFILE_SHA)
        for change in ("none", "source", "support", "identity", "original", "extra", "missing-source", "missing-identity"):
            with self.subTest(change=change):
                files = {"worker.py": (ROOT / "scripts/provider_conformance_worker.py").read_bytes(),
                         "support.py": (ROOT / "scripts/tau_trial_worker.py").read_bytes(),
                         "model-profile.py": source}
                identity = dict(SELECTED)
                if change == "source": files["model-profile.py"] += b"\n"
                if change == "support": files["support.py"] += b"\n"
                if change == "identity": identity["served_model_name"] = worker.MODEL
                if change == "original": identity = profiles.select()
                if change == "extra": identity["endpoint"] = "untrusted"
                if change == "missing-source": del files["model-profile.py"]
                plan = {"schema": "nib.private-provider-payload/v1", "requirements_sha256": worker.LOCK_SHA,
                        "checker_wheel_sha256": worker.CHECKER_SHA, "model_profile": identity,
                        "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                  for name, raw in files.items()}}
                if change == "missing-identity": del plan["model_profile"]
                raw = prep.runtime.encoded(plan)
                dest = self.root / change
                prep.publish(dest, {**files, "trial.json": raw})
                sha = hashlib.sha256(raw).hexdigest()
                if change == "none":
                    self.assertEqual(worker.verify(dest / "payload", sha), plan)
                else:
                    with self.assertRaises(ValueError):
                        worker.verify(dest / "payload", sha)


class SelectedSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.PayloadProfiles.setUpClass()
        cls.extra = dict(models.PayloadProfiles.extra)
        for name in (host.PROVIDER, host.PROVIDER_WORKER):
            cls.extra[name] = (ROOT / name).read_bytes()
        cls.args = (*models.PayloadProfiles.args[:3], cls.extra, *models.PayloadProfiles.args[4:7], "nonconfidential", 2)
        cls.options = {"model_profile": "uncensored", "tokenizer_parity": True,
                       "tokenizer_materialization": True, "runtime_observation": True}
        cls.settings = original_contract()["provider_conformance"]
        cls.files, cls.contract = host.make_payload(*cls.args, **cls.options, provider_conformance=cls.settings)

    def test_selected_checker_has_same_runtime_and_ordinary_security_limits(self):
        _, baseline = host.make_payload(*self.args, **self.options)
        for key in ("arguments", "environment", "model_profile", "pack_revision", "manifest_sha256", "pack_bytes"):
            self.assertEqual(self.contract[key], baseline[key])
        self.assertFalse(self.contract["confidential"])
        self.assertFalse(self.contract["deployable"])
        plan = models.PayloadProfiles.fixture.builder.recorder.prep.launch_plan()
        with payload_tree(self.files) as root, mock.patch.object(lab, "read_source", side_effect=
                lambda path, *_: self.files[path.removeprefix("/opt/nib/")]):
            self.assertEqual(host.validate_model_profile(self.contract, root), SELECTED)
            self.assertEqual(lab.validate_settings(self.contract, plan, 2), SELECTED)
            self.assertEqual(host.memory_gib(self.contract, root), 512)
        session.enabled(self.contract)
        session.bind_model({"model_profile": SELECTED}, self.contract)
        session.bind_model({}, original_contract())

    def test_classic_profile_is_inside_source_bound_selected_contract(self):
        settings = {**self.settings, "image_profile": session.CLASSIC_PROFILE}
        files, selected = host.make_payload(*self.args, **self.options, provider_conformance=settings)
        session.enabled(selected)
        self.assertEqual(selected["provider_conformance"], settings)
        self.assertNotEqual(files["lab.json"], self.files["lab.json"])
        for key in ("arguments", "environment", "model_profile", "pack_revision", "manifest_sha256", "pack_bytes"):
            self.assertEqual(selected[key], self.contract[key])
        self.assertEqual(selected["sources"]["provider-session.py"],
                         hashlib.sha256((ROOT / host.PROVIDER).read_bytes()).hexdigest())

    def test_cross_checkpoint_binding_and_unreviewed_modes_rejected(self):
        for plan in ({}, {"model_profile": None}, {"model_profile": profiles.select()},
                     {"model_profile": {**SELECTED, "hf_revision": "0" * 40}}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                session.bind_model(plan, self.contract)
        with self.assertRaises(ValueError):
            session.bind_model({"model_profile": SELECTED}, original_contract())
        for change in ({"gpu_security": "confidential"}, {"gpu_family": "B200"},
                       {"data_parallel_size": True}, {"data_parallel_size": 1},
                       {"model_profile": profiles.select()}, {"tokenizer_parity": False},
                       {"runtime_observation": False}, {"stream_cohort": {}},
                       {"execution_profile": "batch8-graphs"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                session.enabled({**self.contract, **change})

    def test_cli_requires_all_measurements_and_exact_profile(self):
        argv = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                "--model-profile", "uncensored", "--tokenizer-parity", "--tokenizer-materialization",
                "--runtime-observation", "--provider-conformance-wheels", "/unused/wheels"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            self.assertEqual(prepare.call_args.kwargs, {**self.options, "provider_wheelhouse": "/unused/wheels"})
        for flag in ("--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"):
            with mock.patch.object(sys, "argv", [arg for arg in argv if arg != flag]), \
                    mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()
        for extra in (["--stream-cohort"], ["--execution-profile", "batch8-eager"]):
            with mock.patch.object(sys, "argv", argv + extra), mock.patch.object(host, "prepare") as prepare, \
                    self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()
