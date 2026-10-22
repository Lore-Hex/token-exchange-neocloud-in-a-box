# SPDX-License-Identifier: Apache-2.0
"""Fixed probe policy and ordinary-budget equivalence; no upstream/model run."""
import copy
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_support_probe as policy
import tau_preflight as tau
import test_tau_preflight as fixtures
import test_tau_trial as trial
from test_evidence_history import git

ROOT = Path(__file__).resolve().parent.parent


class SupportPolicyTests(unittest.TestCase):
    def test_cpu_loader_works_with_only_its_exact_declared_source_closure(self):
        code = r'''
import pathlib,runpy,shutil,sys,tempfile
root=pathlib.Path(sys.argv[1])
rec=runpy.run_path(str(root/'scripts/record-tau-cpu-parity.py'))
with tempfile.TemporaryDirectory() as temporary:
    target=pathlib.Path(temporary).resolve()
    for name in rec['SOURCES']:
        output=target/name
        output.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(root/name,output)
    for name in ('tau_support_probe','tau_cpu_probe','tau_cpu_session','tau_preflight','tau_trial_worker'):
        sys.modules[name]=object()
    cpu=rec['toolkit'](target)
    policy=sys.modules['tau_support_probe']
    policy.validate(policy.configuration())
    assert set(cpu.MODEL_IDS)==set(policy.MODELS)
    assert not any(k=='tau2' or k.startswith('tau2.') or k=='litellm' for k in sys.modules)
'''
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT)],
                                timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_resource_schema_has_no_reference_or_prices_and_cannot_expand(self):
        value = policy.configuration()
        self.assertEqual(set(value["support"]), {"simulator", "evaluator"})
        self.assertEqual(value["pricing"], "not-measured")
        self.assertNotIn("cost_usd", value["limits"])
        self.assertEqual(value["limits"]["requests"], 3)
        for mutate in (lambda r: r.update(reference={}), lambda r: r.update(pricing="free"),
                       lambda r: r["limits"].update(requests=4), lambda r: r["limits"].update(trial_seconds=241),
                       lambda r: r["limits"].update(input_tokens_per_request=4097),
                       lambda r: r["generation"].update(max_tokens=257), lambda r: r.update(live_admission=True),
                       lambda r: r["selection"].update(seed=True), lambda r: r["selection"].update(trial=False)):
            row = copy.deepcopy(value)
            mutate(row)
            with self.assertRaises(ValueError): policy.validate(row)
        timer = [0.0]
        budget = policy.ResourceBudget(value, clock=lambda: timer[0])
        budget.begin_trial()
        for role in ("simulator", "candidate", "evaluator"):
            reserved = budget.reserve(role)
            self.assertEqual(reserved, {"timeout_seconds": 120, "max_tokens": 256, "num_retries": 0})
            self.assertEqual(budget.pending[1:3], (4096, 256))
            budget.settle({"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110})
        self.assertEqual(budget.requests, 3)
        with self.assertRaises(ValueError): budget.reserve("simulator")

    def test_production_budget_exact_legacy_reserve_settle_and_abandon_behavior(self):
        raw = git(ROOT, "show", "ea1de780008a7f81b1e54d2df3012c437f109830:scripts/tau_preflight.py")
        self.assertEqual(tau.digest(raw), "37a6c53c31f78f952ca8abf47ee96f166feaaf1a83cf8171c76caa72637caeec")
        legacy = types.ModuleType("legacy_tau_preflight")
        legacy.__file__ = str(ROOT / "scripts/tau_preflight.py")
        exec(compile(raw, legacy.__file__, "exec", dont_inherit=True), legacy.__dict__)
        def trace(module, mode):
            timer = [0.0]
            budget = module.Budget(fixtures.configuration(), clock=lambda: timer[0])
            events = []
            budget.begin_trial()
            try:
                for role in ("simulator", "candidate", "evaluator", "reference"):
                    events.append(budget.reserve(role))
                    if mode == "timeout": timer[0] = budget.pending[4]
                    if mode == "abandon":
                        budget.abandon()
                        budget.reserve(role)
                    events.append(budget.settle({"prompt_tokens": 7, "completion_tokens": 1,
                                                 "total_tokens": 9 if mode == "bad-usage" else 8}))
                budget.end_trial()
            except ValueError:
                events.append("rejected")
            events.append({key: str(getattr(budget, key)) for key in
                ("spent", "tokens", "requests", "pending", "failed", "trial_deadline", "turns")})
            return tau.encoded(events)
        for mode in ("pass", "timeout", "abandon", "bad-usage"):
            with self.subTest(mode=mode): self.assertEqual(trace(legacy, mode), trace(tau, mode))

    def test_task_two_requires_authenticated_base_membership_and_one_assertion(self):
        prefix = "data/tau2/domains/retail/"
        tasks = [{"id": "2", "initial_state": None, "user_tools": None,
                  "evaluation_criteria": {"nl_assertions": ["Synthetic assertion"]}}]
        tasks += [{"id": str(number)} for number in (0, 1, 3, 4)]
        base = [row["id"] for row in tasks]
        def source(rows, split):
            return {prefix + "tasks.json": tau.encoded(rows), prefix + "split_tasks.json": tau.encoded({"base": split})}
        self.assertEqual(policy.source_selection(source(tasks, base)), policy.SELECTION)
        for rows, split in ((tasks, []), (tasks, ["0"]), (tasks * 2, ["2"]),
                            ([{**tasks[0], "initial_state": {}}], ["2"]),
                            ([{**tasks[0], "evaluation_criteria": {"nl_assertions": []}}], ["2"])):
            with self.assertRaises(ValueError): policy.source_selection(source(rows, split))

    def test_hosted_setup_uses_only_already_locked_public_urls_sizes_and_hashes(self):
        prep = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/prepare-tau-cpu-probe-native.py")))
        runner = prep.rec.load(ROOT / "scripts/tau_trial.py", "test_probe_setup")
        archive, wheels = prep.plan(runner.runtime)
        catalog = tau.json_value((ROOT / runner.runtime.CATALOG).read_bytes())
        self.assertEqual(archive, ("https://codeload.github.com/sierra-research/tau2-bench/tar.gz/" + tau.REVISION,
                                   92081935, runner.runtime.ARCHIVE_SHA))
        self.assertEqual(wheels, {row["filename"]: (row["url"], row["bytes"], row["sha256"])
                                 for row in catalog["wheels"].values()})
        self.assertLessEqual(sum(row[1] for row in wheels.values()), runner.runtime.WHEELS_LIMIT)
        with mock.patch.object(Path, "read_bytes", return_value=b"{}"), self.assertRaises(ValueError):
            prep.plan(runner.runtime)

    def test_typed_plan_requires_fixed_input_and_authenticated_task_not_smoke_selection(self):
        runner = trial.runner
        policy_path = "scripts/tau_support_probe.py"
        prefix = "data/tau2/domains/retail/"
        tasks = [{"id": str(number), "evaluation_criteria": {"nl_assertions": ["Synthetic assertion"]}}
                 for number in (0, 1, 2, 3, 4)]
        sources = {prefix + "tasks.json": tau.encoded(tasks),
                   prefix + "split_tasks.json": tau.encoded({"base": [row["id"] for row in tasks]})}
        pins = {"files": {name: {"sha256": tau.digest(raw), "bytes": len(raw)} for name, raw in sources.items()}}
        config = policy.configuration()
        files = {runner.WORKER: (ROOT / runner.WORKER).read_bytes(), policy_path: (ROOT / policy_path).read_bytes(),
                 "manifest.json": tau.encoded(config), "inputs.json": b"{}\n",
                 **{"upstream/" + name: raw for name, raw in sources.items()}}
        toolkit = {"git_sha": "a" * 40, "files": {name: tau.digest(files[name]) for name in (runner.WORKER, policy_path)}}
        template = {"schema": "nib.tau-cpu-probe-plan/v1", "support_probe": True, "transport": "scripted-parent-pipe",
            "image": runner.runtime.TARGET["image"], "platform": runner.runtime.TARGET["platform"],
            "manifest_sha256": tau.digest(files["manifest.json"]), "selection": dict(policy.SELECTION),
            "environment": runner.ENVIRONMENT, "seconds": runner.SECONDS,
            "producer": {"git_sha": toolkit["git_sha"], "adapter_sha256": toolkit["files"][runner.WORKER],
                         "runtime_receipt_sha256": tau.digest(files["inputs.json"])},
            "files": {name: {"bytes": len(raw), "sha256": tau.digest(raw)} for name, raw in files.items()}}
        read = runner.runtime.read_file
        def trusted_pins(path, limit, deadline):
            return tau.encoded(pins) if path == runner.ROOT / tau.PINS else read(path, limit, deadline)
        for mutation in (None, lambda p: p.update(schema="nib.tau-scripted-plan/v1"),
                         lambda p: p.update(support_probe=False), lambda p: p["selection"].update(task_id="1"),
                         lambda p: p["producer"].update(git_sha="e" * 40), lambda p: p.update(extra="private-canary")):
            with tempfile.TemporaryDirectory() as temp, mock.patch.object(runner.runtime, "read_file", side_effect=trusted_pins):
                payload = Path(temp).resolve() / "payload"
                plan = copy.deepcopy(template)
                if mutation: mutation(plan)
                for name, raw in {**files, "trial.json": tau.encoded(plan)}.items():
                    path = payload / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                args = (payload, tau.digest(tau.encoded(plan)), toolkit, time.monotonic() + 5)
                if mutation is None:
                    self.assertEqual(runner.validate_plan(*args, parent_pipe=True, support_probe=True), (plan, config))
                else:
                    with self.assertRaises((ValueError, trial.worker.Abort)):
                        runner.validate_plan(*args, parent_pipe=True, support_probe=True)
                with self.assertRaises((ValueError, trial.worker.Abort)):
                    runner.validate_plan(*args, parent_pipe=True)

    def test_original_builder_and_evaluator_receive_actual_trajectory_without_full_trial(self):
        names = ("tau2", "tau2.runner", "tau2.runner.helpers", "tau2.runner.build", "tau2.data_model",
                 "tau2.data_model.simulation", "tau2.evaluator", "tau2.evaluator.evaluator_nl_assertions", "tau2.utils")
        modules = {name: types.ModuleType(name) for name in names}
        task = types.SimpleNamespace(initial_state=None, user_tools=None,
            evaluation_criteria=types.SimpleNamespace(nl_assertions=["Synthetic assertion"]))
        modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        modules["tau2.data_model.simulation"].TextRunConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
        orchestrator = mock.Mock(done=False)
        trajectory = ["private-canary"]
        orchestrator.get_messages.return_value = trajectory
        builder = modules["tau2.runner.build"].build_text_orchestrator = mock.Mock(return_value=orchestrator)
        evaluator = mock.Mock()
        evaluator.calculate_reward.return_value = types.SimpleNamespace(reward=0.0)
        modules["tau2.evaluator.evaluator_nl_assertions"].NLAssertionsEvaluator = evaluator
        modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock())
        bridge = mock.Mock()
        with mock.patch.dict(sys.modules, modules), mock.patch.object(trial.worker, "configure_upstream") as configure:
            self.assertEqual(trial.worker.run_support_probe(None, policy.configuration(), policy.SELECTION, bridge), 0.0)
        configure.assert_called_once_with(bridge)
        orchestrator.initialize.assert_called_once_with()
        self.assertEqual(orchestrator.step.call_count, 2)
        evaluator.calculate_reward.assert_called_once_with(task, trajectory)
        config = builder.call_args.args[0]
        self.assertEqual((config.max_steps, config.max_retries, config.hallucination_retries), (2, 0, 0))
        self.assertFalse(config.auto_review or config.auto_resume or config.verbose_logs)
        self.assertIsNone(config.save_to)
        self.assertEqual(config.timeout, 180)
        self.assertEqual(bridge.assertions, ["Synthetic assertion"])

if __name__ == "__main__":
    unittest.main()
