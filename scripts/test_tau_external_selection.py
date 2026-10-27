# SPDX-License-Identifier: Apache-2.0
"""Staged preflight/dispatch with synthetic pinned inputs; no service calls."""

import copy
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import random
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_external_selection as selection
import test_tau_external_trial as full

tau, policy, external, runner, worker = selection.tau, full.policy, full.external, full.runner, full.worker
BUDGET = {"weekly_cap_usd": "10000", "gpu_committed_usd": "9176",
          "cpu_disk_reserved_usd": "100", "other_committed_usd": "50"}


def fixtures(domains=None, trials=1, inputs=None, unsupported=False):
    domains = ["retail"] if domains is None else domains
    sources = {}
    for domain in domains:
        tasks = [{"id": str(n), "evaluation_criteria": {"nl_assertions": ["SYNTHETIC_ASSERTION_CANARY"]}}
                 for n in (9, 2, 1, 0, 11, 10)]
        if unsupported:
            tasks[3]["evaluation_criteria"]["nl_assertions"] = []
        name, splits = selection.source_names([domain])
        sources[name] = tau.encoded({"tasks": tasks})
        sources[splits] = tau.encoded({"base": [row["id"] for row in tasks]})
    pins = {"revision": tau.REVISION, "files": {name: {
        "bytes": len(raw), "sha256": tau.digest(raw),
        "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
        for name, raw in sources.items()}}
    plan = selection.build(sources, pins, "a" * 40, domains, trials, 42, inputs or BUDGET)
    return plan, sources, pins


def stage(**kwargs):
    return {"plan": fixtures(**kwargs)[0], "index": 0}


def configuration(value=None):
    return policy.configuration(external_simulator=True, full_task=True, stage=stage() if value is None else value)


def runtime(value=None):
    held = full.runtime()
    config = configuration(value)
    held.manifest.update(schema=selection.TRANSPORT_SCHEMA, input=config, settings=policy.settings(config))
    return held


class SelectionTests(unittest.TestCase):
    def test_deterministic_first_five_lexicographic_and_trial_seeds(self):
        plan, sources, pins = fixtures(list(tau.DOMAINS), 4, {**BUDGET, "gpu_committed_usd": "0"})
        self.assertEqual(selection.validate(plan), plan)
        selection.verify_sources(plan, sources, pins)
        rng = random.Random(42)
        self.assertEqual(plan["trial_seeds"], [rng.randint(0, 1000000) for _ in range(4)])
        self.assertEqual(len(plan["selections"]), 60)
        self.assertEqual(plan["domain_tasks"]["airline"]["task_ids"], ["0", "1", "10", "11", "2"])
        self.assertEqual(plan["selections"][4]["selection"], {
            "domain": "airline", "task_id": "1", "trial": 0, "seed": plan["trial_seeds"][0], "role": "candidate"})
        self.assertEqual(plan, selection.build(dict(reversed(list(sources.items()))), pins, "a" * 40,
                                               list(tau.DOMAINS), 4, 42, {**BUDGET, "gpu_committed_usd": "0"}))
        self.assertNotIn(b"CANARY", tau.encoded(plan))
        for name in ("execution_authorized", "benchmark_executed", "quality_eligible", "full_benchmark", "confidential", "live_admission"):
            self.assertIs(plan[name], False)

    def test_reservations_match_live_unchanged_count_and_generation_limits(self):
        plan = fixtures()[0]
        self.assertEqual(selection.COUNT_RESERVATION, external.COUNT_RESERVATION)
        live = full.configuration()
        expected = external.COUNT_RESERVATION * live["limits"]["requests"] + Decimal(live["limits"]["cost_usd"])
        self.assertEqual(selection.TRIAL_RESERVATION, expected)
        self.assertEqual(plan["budget"]["total_usd_per_trial"], "70.044864")
        self.assertEqual(plan["budget"]["trials_reserved_usd"], "350.224320")
        self.assertEqual(plan["budget"]["remaining_usd"], "323.775680")
        self.assertIs(plan["budget"]["within_cap"], True)

    def test_all_domains_exceed_current_cap_no_silent_subselection(self):
        plan = fixtures(list(tau.DOMAINS))[0]
        self.assertEqual(plan["result"], "fail")
        self.assertEqual(len(plan["selections"]), 15)
        self.assertEqual(plan["budget"]["trials_reserved_usd"], "1050.672960")
        self.assertEqual(plan["budget"]["remaining_usd"], "-376.672960")
        with self.assertRaises(ValueError):
            selection.selected({"plan": plan, "index": 0})
        self.assertEqual(selection.validate(plan), plan)

    def test_exact_budget_boundary_and_nonfinite_or_missing_commitments(self):
        inputs = {**BUDGET, "cpu_disk_reserved_usd": "0", "other_committed_usd": "0",
                  "weekly_cap_usd": "9526.224320"}
        self.assertTrue(fixtures(inputs=inputs)[0]["budget"]["within_cap"])
        inputs["weekly_cap_usd"] = "9526.224319"
        self.assertFalse(fixtures(inputs=inputs)[0]["budget"]["within_cap"])
        for key in BUDGET:
            for bad in (None, True, 0, 0.0, "NaN", "Infinity", "-1", "1e3", "10000.000001", "0.0000000001"):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    fixtures(inputs={**BUDGET, key: bad})
            with self.assertRaises(ValueError):
                fixtures(inputs={name: value for name, value in BUDGET.items() if name != key})

    def test_closed_domains_trials_seeds_indices(self):
        for domains in ([], ["other"], ["retail", "retail"], ["retail", "airline"], [True]):
            with self.assertRaises(ValueError):
                fixtures(domains)
        for trials in (False, 0, 5, 1.0, "1"):
            with self.assertRaises(ValueError):
                fixtures(trials=trials)
        plan, sources, pins = fixtures()
        for seed in (-1, 1000001, True, 42.0):
            with self.assertRaises(ValueError):
                selection.build(sources, pins, "a" * 40, ["retail"], 1, seed, BUDGET)
        for index in (-1, 5, True, 1.0, "0"):
            with self.assertRaises(ValueError):
                selection.selected({"plan": plan, "index": index})

    def test_unsupported_shapes_retained_not_repaired_or_replaced(self):
        plan, sources, pins = fixtures(unsupported=True)
        self.assertEqual(plan["unsupported_trials"], 1)
        self.assertEqual(len(plan["selections"]), 5)
        self.assertEqual(plan["result"], "fail")
        self.assertEqual(plan["selections"][0]["compatibility"], "unsupported-assertion-shape")
        with self.assertRaises(ValueError):
            configuration({"plan": plan, "index": 0})
        self.assertEqual(configuration({"plan": plan, "index": 1})["selection"]["task_id"], "1")
        selection.verify_sources(plan, sources, pins)

    def test_tampering_cannot_broaden_selection_budget_claims_or_source(self):
        original, sources, pins = fixtures()
        mutations = (lambda p: p.update(extra="private"), lambda p: p.update(execution_authorized=True),
            lambda p: p.update(upstream_revision="f" * 40), lambda p: p.update(quality_eligible=True),
            lambda p: p["selections"][0]["selection"].update(role="reference"),
            lambda p: p["trial_seeds"].__setitem__(0, 42), lambda p: p["budget"].update(within_cap=False),
            lambda p: p["budget"].update(count_usd_per_attempt="0"),
            lambda p: p["budget"].update(trials_reserved_usd="0"),
            lambda p: p["domain_tasks"]["retail"]["assertion_counts"].__setitem__(0, True))
        for mutate in mutations:
            value = copy.deepcopy(original)
            mutate(value)
            with self.assertRaises(ValueError):
                selection.validate(value)
        changed = copy.deepcopy(original)
        changed["source_files"][selection.source_names(["retail"])[0]]["sha256"] = "c" * 64
        selection.validate(changed)
        with self.assertRaises(ValueError):
            selection.verify_sources(changed, sources, pins)
        for key in ("bytes", "sha256", "git_blob"):
            bad = copy.deepcopy(pins)
            name = selection.source_names(["retail"])[0]
            bad["files"][name][key] = 1 if key == "bytes" else "0" * (40 if key == "git_blob" else 64)
            with self.assertRaises(ValueError):
                selection.build(sources, bad, "a" * 40, ["retail"], 1, 42, BUDGET)

    def test_original_full_task_configuration_bytes_unchanged(self):
        original = subprocess.run(["git", "show", "fd380d29456816f329dbffca0c46a67c4c6530f8:scripts/tau_selected_policy.py"],
                                  cwd=runner.ROOT, capture_output=True, check=True, timeout=10).stdout
        before = types.ModuleType("reviewed_full_task")
        exec(compile(original, "<reviewed-full-task>", "exec"), before.__dict__)
        self.assertEqual(tau.encoded(before.configuration(external_simulator=True, full_task=True)),
                         tau.encoded(full.configuration()))
        config = configuration()
        policy.validate(config)
        self.assertEqual({k: v for k, v in config.items() if k not in ("schema", "selection", "stage")},
                         {k: v for k, v in full.configuration().items() if k not in ("schema", "selection")})
        with self.assertRaises(ValueError):
            policy.configuration(stage=stage())

    def test_staged_transport_schema_and_bindings_are_closed(self):
        held = runtime()
        policy.transport_manifest(held.manifest, tau.digest(tau.encoded(held.manifest)))
        for change in (lambda m: m.update(schema=policy.EXTERNAL_TRIAL_TRANSPORT),
                       lambda m: m["input"]["selection"].update(task_id="9"),
                       lambda m: m["input"].update(stage=None)):
            altered = copy.deepcopy(held.manifest)
            change(altered)
            with self.assertRaises(ValueError):
                policy.transport_manifest(altered, tau.digest(tau.encoded(altered)))

    def test_staged_runtime_rejects_budget_before_private_reads(self):
        with mock.patch.object(external.private, "private_read") as read, \
                mock.patch.object(external.bounded_https, "HTTPS") as http, self.assertRaises(ValueError):
            external.Runtime(*(None,) * 7, time.monotonic() + 90, full_task=True,
                             stage=stage(domains=list(tau.DOMAINS)))
        read.assert_not_called()
        http.assert_not_called()

    def test_verified_parent_closure_rejects_ambient_staged_helper(self):
        names = ("tau_preflight", "tau_trial_worker", "tau_aggregate", "tau_transport",
                 *runner.SELECTED_PARENT_MODULES, "tau_pipe_driver", "tau_selected_driver", *runner.EXTERNAL_PARENT_MODULES)
        toolkit = {"files": {"scripts/" + name + ".py": tau.digest(
            (runner.ROOT / ("scripts/" + name + ".py")).read_bytes()) for name in names}}
        expected = configuration()
        with mock.patch.dict(sys.modules, {"tau_external_selection": object()}):
            driver = runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)
            self.assertEqual(driver.policy.configuration(external_simulator=True, full_task=True, stage=stage()), expected)
        toolkit["files"]["scripts/tau_external_selection.py"] = "c" * 64
        with self.assertRaises(ValueError):
            runner.parent_driver(toolkit, selected_operational=True, external_simulator=True)

    def test_run_requires_explicit_matching_plan_and_index_before_docker(self):
        config = configuration()
        good = selection.binding(config["stage"])
        for binding in (None, {**good, "index": 1}, {**good, "sha256": "0" * 64}, {**good, "index": False}):
            with mock.patch.object(runner, "identity", return_value={}), \
                    mock.patch.object(runner, "validate_plan", return_value=({}, config)), \
                    mock.patch.object(runner, "parent_driver", return_value=external), \
                    mock.patch.object(runner.probe, "capture") as command, self.assertRaises(ValueError):
                runner.run(None, None, None, None, parent_pipe=True,
                    external_inputs={"deadline": time.monotonic() + 90, "arguments": (None,) * 7,
                                     "full_task": True, **({"stage_binding": binding} if binding is not None else {})})
            command.assert_not_called()
        with mock.patch.object(runner, "identity", return_value={}), \
                mock.patch.object(runner, "validate_plan", return_value=({}, full.configuration())), \
                mock.patch.object(runner, "parent_driver", return_value=external), self.assertRaises(ValueError):
            runner.run(None, None, None, None, parent_pipe=True, external_inputs={"deadline": time.monotonic() + 90,
                "arguments": (None,) * 7, "full_task": True, "stage_binding": good})

    def test_parent_session_retains_actual_domain_trial_and_scored_zero(self):
        for domain in tau.DOMAINS:
            value = stage(domains=[domain], trials=2,
                          inputs={**BUDGET, "cpu_disk_reserved_usd": "0", "other_committed_usd": "0"})
            value["index"] = 1
            held, channel = runtime(value), full.lookup.channels.FakeChannel()
            session = external.Session(channel, held, assertions=["synthetic"])
            full.complete_roles(session, channel)
            channel.packets.append(full.terminal(session))
            self.assertTrue(session.serve_once())
            self.assertEqual(session.result["selection"], selection.selected(value))
            self.assertEqual(session.result["schema"], "nib.tau-external-staged-trial/v1")
            self.assertEqual(session.result["reward"], "0.0")
            self.assertNotIn("CANARY", str(session.result))
            session.close()

    def test_worker_reuses_original_entrypoint_limits_and_records_unsupported_trajectory(self):
        for domain in tau.DOMAINS:
            for reason in ("user_stop", "too_many_errors"):
                config = configuration(stage(domains=[domain]))
                channel = full.termination.Channel()
                proxy = full.pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
                with full.termination.upstream(full.termination.simulation(reason, int(reason == "too_many_errors"))) as run, \
                        mock.patch.object(worker, "text_config", wraps=worker.text_config) as text_config:
                    full.pipe.execute(None, config, config["selection"], proxy, run=worker.run_selected_upstream)
                    run.assert_called_once()
                    self.assertEqual(run.call_args.kwargs["evaluation_type"], "all-nl")
                    self.assertFalse(run.call_args.kwargs["auto_review"])
                    self.assertEqual(text_config.call_args.args[0], config["selection"])
                    self.assertEqual(text_config.call_args.args[1]["limits"], full.configuration()["limits"])
                result = channel.sent[-1]
                self.assertEqual(result["upstream_termination"]["reason"], reason)
                self.assertEqual(result["status"] == "evaluated", reason == "user_stop")
                self.assertNotIn("CANARY", str(result))

    def test_read_stage_hash_canonical_bytes_and_no_overwrite(self):
        plan = fixtures()[0]
        raw = tau.encoded(plan)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "plan.json"
            path.write_bytes(raw)
            self.assertEqual(runner.read_stage(path, tau.digest(raw), 0, time.monotonic() + 5), {"plan": plan, "index": 0})
            with self.assertRaises(ValueError):
                runner.read_stage(path, "0" * 64, 0, time.monotonic() + 5)
            path.write_bytes(b" " + raw)
            with self.assertRaises(ValueError):
                runner.read_stage(path, tau.digest(b" " + raw), 0, time.monotonic() + 5)

    def test_verified_source_archive_preflight_never_loads_upstream_or_endpoints(self):
        plan, sources, pins = fixtures()
        pins_raw = tau.encoded(pins)
        catalog = {"wheels": {}, "root": {"mode": 493}}
        raw_catalog = tau.encoded(catalog)
        def read(path, limit, deadline):
            return pins_raw if path == runner.ROOT / tau.PINS else raw_catalog if path == runner.ROOT / runner.runtime.CATALOG else b"archive"
        with mock.patch.object(runner, "identity", return_value={"git_sha": "a" * 40}), \
                mock.patch.object(runner, "selected_policy", return_value=policy), \
                mock.patch.object(runner.runtime, "read_file", side_effect=read), \
                mock.patch.object(runner.tau, "PINS_SHA", tau.digest(pins_raw)), \
                mock.patch.object(runner.runtime, "CATALOG_SHA", tau.digest(raw_catalog)), \
                mock.patch.object(runner.runtime, "source_inputs", return_value=({}, sources)) as source, \
                mock.patch.object(runner.runtime, "make_catalog", return_value=catalog), \
                mock.patch.object(runner, "parent_driver") as driver:
            self.assertEqual(runner.selection_plan(Path("/archive"), ["retail"], 1, 42, BUDGET, time.monotonic() + 5), plan)
            source.assert_called_once()
            self.assertEqual(source.call_args.args[:2], (b"archive", pins))
            driver.assert_not_called()

    def test_prepare_and_parent_validation_bind_plan_source_and_actual_selection(self):
        value, sources, pins = fixtures(["airline"])
        selected_stage = {"plan": value, "index": 3}
        _, toolkit, _, _, _ = full.lookup.scorer.fixture()
        pins_raw, catalog = tau.encoded(pins), {"wheels": {}, "root": {"mode": 493}}
        raw_catalog = tau.encoded(catalog)
        real_read = runner.runtime.read_file
        def read(path, limit, deadline):
            if path == runner.ROOT / tau.PINS: return pins_raw
            if path == runner.ROOT / runner.runtime.CATALOG: return raw_catalog
            if path == Path("/archive"): return b"archive"
            return real_read(path, limit, deadline)
        with mock.patch.object(runner, "identity", return_value=toolkit), \
                mock.patch.object(runner.runtime, "read_file", side_effect=read), \
                mock.patch.object(runner.tau, "PINS_SHA", tau.digest(pins_raw)), \
                mock.patch.object(runner.runtime, "CATALOG_SHA", tau.digest(raw_catalog)), \
                mock.patch.object(runner.runtime, "source_inputs", return_value=({}, sources)), \
                mock.patch.object(runner.runtime, "make_catalog", return_value=catalog):
            args = (Path("/archive"), Path("/unused"), None, None, None, time.monotonic() + 10)
            kwargs = {"parent_pipe": True, "selected_operational": True, "external_simulator": True, "full_task": True}
            files, contract = runner.prepare(*args, **kwargs, stage=selected_stage)
            self.assertEqual(contract["selection"], selection.selected(selected_stage))
            manifest = tau.json_value(files["manifest.json"])
            self.assertEqual(manifest, configuration(selected_stage))
            with tempfile.TemporaryDirectory() as temp:
                destination = Path(temp).resolve() / "prepared"
                runner.publish(destination, files)
                checked = runner.validate_plan(destination / "payload", tau.digest(files["trial.json"]), toolkit,
                                               time.monotonic() + 5, parent_pipe=True, selected_operational=True)
                self.assertEqual(checked, (contract, manifest))
            for mutate in (lambda p: p.update(producer_git_sha="b" * 40),
                           lambda p: p["source_files"][next(iter(p["source_files"]))].update(sha256="c" * 64)):
                bad = copy.deepcopy(selected_stage)
                mutate(bad["plan"])
                with self.assertRaises(ValueError):
                    runner.prepare(*args, **kwargs, stage=bad)

    def test_worker_keeps_single_assertion_guard_and_does_not_repair(self):
        config = configuration(stage(domains=["telecom"]))
        for assertions in ([], ["synthetic", "second"]):
            channel = full.termination.Channel()
            proxy = full.pipe.transport.WorkerProxy(channel, time.monotonic() + 5, selected_operational=True)
            with full.termination.upstream(full.termination.simulation("user_stop")) as run:
                task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=assertions))
                with mock.patch.object(sys.modules["tau2.runner.helpers"], "get_tasks", return_value=[task]):
                    full.pipe.execute(None, config, config["selection"], proxy, run=worker.run_selected_upstream)
                run.assert_not_called()
            self.assertNotEqual(channel.sent[-1]["status"], "evaluated")
            self.assertIsNone(channel.sent[-1]["reward"])

    def test_cli_preserves_failed_budget_plan_privately_and_exclusively(self):
        entry = runner.load("scripts/record-tau-external.py")
        plan = fixtures(list(tau.DOMAINS))[0]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp).resolve() / "result"
            argv = ["record-tau-external.py", "plan", "--source-archive", "/unused", "--output", str(output),
                    "--domains", *tau.DOMAINS, "--trials", "1", "--seed", "42"]
            for key, value in BUDGET.items():
                argv.extend(["--" + key.replace("_", "-"), value])
            with mock.patch.object(sys, "argv", argv), mock.patch.object(entry.trial, "selection_plan", return_value=plan) as build, \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(entry.main(), 1)
                self.assertEqual(build.call_args.args[1:5], (list(tau.DOMAINS), 1, 42, BUDGET))
                before = {p.name: p.read_bytes() for p in output.iterdir()}
                self.assertEqual(entry.main(), 1)
                self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertEqual(tau.json_value(before["output.log"]), plan)
            self.assertEqual(tau.json_value(before["run.json"])["result"], "fail")
            self.assertEqual(tau.json_value(before["run.json"])["schema"], "nib.evidence/v1")
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertNotIn(b"CANARY", path.read_bytes())

    def test_cli_staged_prepare_requires_all_explicit_flags(self):
        entry = runner.load("scripts/record-tau-external.py")
        argv = ["record-tau-external.py", "prepare", "--source-archive", "/unused", "--wheelhouse", "/unused",
                "--output", "/unused", "--selection-plan", "/unused", "--selection-plan-sha256", "a" * 64,
                "--selection-index", "0", "--full-task"]
        for flag in (None, "--full-task", "--selection-plan", "--selection-plan-sha256", "--selection-index"):
            args = list(argv)
            if flag is not None:
                index = args.index(flag)
                del args[index:index + (1 if flag == "--full-task" else 2)]
            with mock.patch.object(sys, "argv", args), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), \
                    mock.patch.object(entry.trial, "read_stage", return_value=stage()) as read, \
                    mock.patch.object(entry.trial, "prepare", return_value=({}, {"producer": {"git_sha": "a" * 40}})) as prepare, \
                    mock.patch.object(entry.trial, "publish"):
                self.assertEqual(entry.main(), 0 if flag is None else 1)
                if flag is None:
                    self.assertEqual(prepare.call_args.kwargs["stage"], stage())
                    read.assert_called_once()
                else:
                    prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
