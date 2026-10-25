# SPDX-License-Identifier: Apache-2.0
"""Fixed 1.7B role binding and original receipt joins; no model execution."""
import copy
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_cpu_probe as probe
import test_tau_cpu_identity as identity
import test_tau_cpu_probe_controller as controller
import test_tau_cpu_identity_workflow as workflow
from test_evidence_history import git

ROOT = Path(__file__).resolve().parents[1]
BASE = "8d001d1f360f35effd80f55df2eb43f43c95422d"
policy = probe.policy


class Identity17Tests(unittest.TestCase):
    def test_parent_report_validator_uses_sealed_policy_not_ambient_module(self):
        runner = controller.trial.runner
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport", "tau_pipe_driver", "tau_support_probe")
        files = {"scripts/" + name + ".py": probe.tau.digest((ROOT / "scripts" / (name + ".py")).read_bytes())
                 for name in names}
        _, docs = identity.prepared(model_17b=True)
        row = identity.passing()
        row.update(schema=policy.IDENTITY_17_REPORT, simulator_binding=policy.runtime_binding(docs["evaluator-runtime.json"]))
        with mock.patch.dict(sys.modules, {"tau_support_probe": object()}):
            driver = runner.parent_driver({"files": files})
            self.assertEqual(driver.validate_support_probe(row), row)
        files["scripts/tau_support_probe.py"] = "0" * 64
        self.assertRaises(ValueError, runner.parent_driver, {"files": files})

    def test_old_profiles_and_all_model_pins_unchanged(self):
        old = types.ModuleType("original_support_policy")
        exec(compile(git(ROOT, "show", BASE + ":scripts/tau_support_probe.py"), "original_policy", "exec"), old.__dict__)
        for followup in (False, True):
            self.assertEqual(probe.tau.encoded(policy.configuration(identity_followup=followup)),
                             probe.tau.encoded(old.configuration(identity_followup=followup)))
        self.assertEqual((ROOT / policy.PINS).read_bytes(), git(ROOT, "show", BASE + ":" + policy.PINS))
        self.assertEqual((ROOT / "scripts/tau_cpu_identity.py").read_bytes(),
                         git(ROOT, "show", BASE + ":scripts/tau_cpu_identity.py"))

    def test_new_profile_is_simulator_only_closed_and_bounded(self):
        value = policy.configuration(identity_followup_17b=True)
        self.assertEqual(set(value["support"]), {"simulator"})
        self.assertEqual(value["support"]["simulator"], policy.configuration()["support"]["evaluator"])
        self.assertEqual(value["owner_roles"], {"simulator": "evaluator"})
        self.assertEqual(value["limits"], policy.configuration()["limits"])
        self.assertEqual(value["generation"], {"temperature": "0", "max_tokens": 256, "num_retries": 0})
        policy.validate(value)
        for mutate in (lambda r: r["support"]["simulator"].update(model=policy.MODELS[0]),
                       lambda r: r["support"]["simulator"].update(revision="a" * 40),
                       lambda r: r["support"].update(evaluator=r["support"]["simulator"]),
                       lambda r: r["owner_roles"].update(simulator="simulator"),
                       lambda r: r["limits"].update(run_seconds=301),
                       lambda r: r["limits"].update(input_tokens_per_request=4097),
                       lambda r: r["limits"].update(output_tokens_per_request=257),
                       lambda r: r["limits"].update(requests=4),
                       lambda r: r["generation"].update(num_retries=1),
                       lambda r: r.update(quality_eligible=True)):
            changed = copy.deepcopy(value)
            mutate(changed)
            with self.assertRaises(ValueError):
                policy.validate(changed)
        for bad in (None, 1, "true"):
            self.assertRaises(ValueError, policy.configuration, identity_followup_17b=bad)
        self.assertRaises(ValueError, policy.configuration, identity_followup=True, identity_followup_17b=True)

    def test_actual_binding_checks_model_tokenizer_process_before_client_creation(self):
        selected, docs = identity.prepared(model_17b=True)
        original = {role: docs[role + "-runtime.json"] for role in ("simulator", "evaluator")}
        for mutate in (lambda r: r["evaluator"].update(model=policy.MODELS[0]),
                       lambda r: r["evaluator"].update(role="simulator"),
                       lambda r: r["evaluator"].update(revision="f" * 40),
                       lambda r: r["evaluator"].update(model_files_sha256="f" * 64),
                       lambda r: r["evaluator"]["gguf"].update(sha256="f" * 64),
                       lambda r: r["evaluator"]["serving"].update(tokenizer_sha256="f" * 64),
                       lambda r: r["evaluator"]["serving"].update(framing_sha256="f" * 64),
                       lambda r: r["evaluator"]["process"].update(pid=98765)):
            runtimes = copy.deepcopy(original)
            endpoints = {role: mock.Mock(port=row["endpoint"]["port"], expected=copy.deepcopy(row["process"]))
                         for role, row in runtimes.items()}
            mutate(runtimes)
            with mock.patch.object(probe, "ProbeClient") as client, self.assertRaises((ValueError, probe.worker.Abort)):
                probe.ProbeSession(mock.Mock(), selected, endpoints, runtimes, time.monotonic() + 3)
            client.assert_not_called()

    def test_metadata_report_requires_binding_and_forbids_evaluator_or_content(self):
        _, docs = identity.prepared(model_17b=True)
        row = identity.passing()
        row.update(schema=policy.IDENTITY_17_REPORT, simulator_binding=policy.runtime_binding(docs["evaluator-runtime.json"]))
        probe.validate(row)
        for mutate in (lambda r: r.pop("simulator_binding"), lambda r: r.update(simulator_binding=None),
                       lambda r: r["simulator_binding"].update(owner_role="simulator"),
                       lambda r: r["simulator_binding"].update(model=policy.MODELS[0]),
                       lambda r: r["simulator_binding"].update(tokenizer_sha256="private-canary"),
                       lambda r: r["simulator_binding"].update(response="private-canary"),
                       lambda r: r["roles"].update(evaluator=1), lambda r: r["roles"].update(agent=1),
                       lambda r: r.update(schema=identity.identity.REPORT),
                       lambda r: r.update(reward=0.0), lambda r: r.update(quality_eligible=True)):
            changed = copy.deepcopy(row)
            mutate(changed)
            with self.assertRaises((ValueError, probe.worker.Abort)):
                probe.validate(changed)

    def test_controller_joins_exact_original_runtime_and_rejects_drift(self):
        control, session = controller.control, controller.session
        for fault in (None, "binding", "receipt", "model", "schema", "cleanup"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary).resolve()
                held, spec, notice, scorer = controller.setup(directory)
                runtime_path = directory / "evaluator-runtime.json"
                runtime = session.llama.worker.decode(runtime_path.read_bytes())
                diagnostic = identity.passing()
                diagnostic.update(schema=policy.IDENTITY_17_REPORT, simulator_binding=policy.runtime_binding(runtime))
                scorer["diagnostic"] = diagnostic
                spec["report_schema"] = policy.IDENTITY_17_REPORT
                if fault == "binding":
                    diagnostic["simulator_binding"]["process_sha256"] = "f" * 64
                elif fault == "receipt":
                    runtime_path.write_bytes(runtime_path.read_bytes() + b" ")
                elif fault == "model":
                    diagnostic["simulator_binding"]["model"] = policy.MODELS[0]
                elif fault == "schema":
                    spec["report_schema"] = identity.identity.REPORT
                elif fault == "cleanup":
                    scorer["cleanup"] = "failed"
                notice.update(scorer_sha256=control.rec.digest(session.tau.encoded(scorer)),
                              **{key: scorer[key] for key in ("result", "cleanup", "diagnostic")})
                controller.control_tests.write(directory / "scorer", "output.log", session.tau.encoded(scorer))
                controller.control_tests.write(directory / "scorer", "run.json",
                                                session.tau.encoded(controller.trial.runner.probe_evidence_record(scorer)))
                args = (session, directory, notice, held, spec, time.monotonic() + 3)
                if fault is None:
                    self.assertEqual(control.probe_receipts(*args), scorer)
                else:
                    with self.assertRaises((ValueError, probe.worker.Abort)):
                        control.probe_receipts(*args)


class Workflow17Tests(unittest.TestCase):
    def test_actual_shell_selects_only_new_profile_and_keeps_controller_argv(self):
        check = workflow.IdentityWorkflowTests()
        check.setUp()
        for index in (1, 2, 3):
            code, output, calls = check.run_step(check.steps[index]["run"], "false", "false", identity_17b="true")
            self.assertEqual(code, 0, output)
            if index == 2:
                self.assertEqual(calls[-1][-1], "--identity-followup-17b")
                self.assertNotIn("--identity-followup", calls[-1])
            if index == 3:
                _, _, old_calls = check.run_step(check.steps[index]["run"], "false", "true")
                self.assertEqual(calls, old_calls)
                self.assertNotIn("--local-identity-followup", calls[0])
        for support, old, new in (("true", "false", "true"), ("false", "true", "true"),
                                  ("false", "false", "unexpected"), ("false", "false", "")):
            code, output, calls = check.run_step(check.steps[1]["run"], support, old, identity_17b=new)
            self.assertNotEqual(code, 0)
            self.assertNotIn(b"DIRECTORY", output)
            self.assertEqual(calls, [])

    def test_preparer_binds_new_schema_without_changing_deadline_or_inputs(self):
        prep = workflow.preparer()
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            runner = mock.Mock()
            runner.runtime.SECONDS, runner.runtime.CATALOG_SHA = 300, "b" * 64
            runner.prepare.return_value = ({"trial.json": b"fixed plan\n"}, {"producer": {"git_sha": "c" * 40}})
            runner.publish.side_effect = lambda path, files: path.mkdir(mode=0o700)
            spec = ("https://example.invalid/archive", 1, "d" * 64)
            deadline = time.monotonic() + 10
            with mock.patch.object(prep.rec, "load", return_value=runner), \
                    mock.patch.object(prep, "plan", return_value=(spec, {})), mock.patch.object(prep.native, "fetch") as fetch:
                report = prep.prepare(work / "prepared", ROOT, work, {}, mock.Mock(), deadline, identity_followup_17b=True)
            self.assertEqual(report["schema"], "nib.tau-cpu-identity-17b-probe-setup/v1")
            self.assertEqual(runner.prepare.call_args.kwargs,
                             {"parent_pipe": True, "support_probe": True, "identity_followup_17b": True})
            self.assertEqual(runner.prepare.call_args.args[-1], deadline)
            self.assertEqual(fetch.call_args.args[-1], deadline)
            self.assertFalse(report["upstream_executed"] or report["quality_eligible"])
            with mock.patch.object(prep.rec, "load") as load:
                self.assertRaises(ValueError, prep.prepare, None, None, None, None, None, deadline,
                                  identity_followup=True, identity_followup_17b=True)
                load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
