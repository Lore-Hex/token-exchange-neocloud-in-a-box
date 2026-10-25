# SPDX-License-Identifier: Apache-2.0
"""Closed selected scorer packaging/dispatch; synthetic interfaces, no GPU score."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_selected_policy as policy
import test_tau_trial as trial

runner, worker, tau = trial.runner, trial.worker, trial.tau
SOURCES = (runner.WORKER, "scripts/deepseek_model_profile.py", "scripts/tau_support_probe.py",
           "scripts/tau_selected_policy.py", "scripts/tau_cpu_support_models.json")


def fixture():
    prefix = "data/tau2/domains/retail/"
    tasks = [{"id": str(n), "evaluation_criteria": {"nl_assertions": ["Synthetic assertion"]}}
             for n in range(5)]
    upstream = {prefix + "tasks.json": tau.encoded(tasks),
                prefix + "split_tasks.json": tau.encoded({"base": [row["id"] for row in tasks]})}
    pins = {"files": {name: {"sha256": tau.digest(raw), "bytes": len(raw)} for name, raw in upstream.items()}}
    files = {name: (runner.ROOT / name).read_bytes() for name in SOURCES}
    toolkit = {"git_sha": "a" * 40, "files": {name: tau.digest(raw) for name, raw in files.items()}}
    config = policy.configuration()
    files.update({"manifest.json": tau.encoded(config), "inputs.json": b"{}\n",
                  **{"upstream/" + name: raw for name, raw in upstream.items()}})
    plan = {"schema": "nib.tau-selected-scorer-plan/v1", "selected_operational": True,
            "transport": "selected-operational-parent-pipe", "image": runner.runtime.TARGET["image"],
            "platform": runner.runtime.TARGET["platform"], "manifest_sha256": tau.digest(files["manifest.json"]),
            "selection": dict(policy.support.SELECTION), "environment": runner.ENVIRONMENT, "seconds": 960,
            "producer": {"git_sha": toolkit["git_sha"], "adapter_sha256": toolkit["files"][runner.WORKER],
                         "runtime_receipt_sha256": tau.digest(files["inputs.json"])},
            "files": {name: {"bytes": len(raw), "sha256": tau.digest(raw)} for name, raw in files.items()}}
    return files, toolkit, config, plan, pins


class SelectedScorerTests(unittest.TestCase):
    def test_explicit_source_closure_and_incompatible_modes(self):
        with mock.patch.object(runner.tau, "committed_identity", return_value={"git_sha": "a" * 40, "files": {}}) as load:
            runner.identity(time.monotonic() + 5, True, selected_operational=True)
            paths = {name for call in load.call_args_list for name in call.kwargs.get("extra_files", ())}
            self.assertTrue(set(SOURCES) <= paths)
            self.assertIn("scripts/test_tau_selected_scorer.py", paths)
            self.assertIn("scripts/test_tau_selected_termination.py", paths)
        for kwargs in ({"parent_pipe": False}, {"live_local": True}, {"support_probe": True},
                       {"agent_prefix": True, "support_probe": True}, {"selected_operational": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                runner.identity(time.monotonic() + 5, **{"parent_pipe": True, "selected_operational": True, **kwargs})
        _, toolkit, _, _, _ = fixture()
        poison = types.SimpleNamespace(configuration=lambda: self.fail("ambient module loaded"))
        with mock.patch.dict(sys.modules, {"tau_selected_policy": poison, "tau_support_probe": poison}):
            self.assertEqual(runner.selected_policy(toolkit).configuration(), policy.configuration())
        toolkit["files"]["scripts/tau_selected_policy.py"] = "0" * 64
        with self.assertRaises(ValueError):
            runner.selected_policy(toolkit)

    def test_plan_rejects_substitution_and_legacy_runner(self):
        files, toolkit, config, template, pins = fixture()
        read = runner.runtime.read_file
        def trusted_pins(path, limit, deadline):
            return tau.encoded(pins) if path == runner.ROOT / tau.PINS else read(path, limit, deadline)
        mutations = (None, lambda p: p.update(schema="nib.tau-scripted-plan/v1"),
                     lambda p: p.update(selected_operational=1), lambda p: p.update(seconds=300),
                     lambda p: p.update(transport="scripted-parent-pipe"),
                     lambda p: p["selection"].update(task_id="1"), lambda p: p["selection"].update(trial=False),
                     lambda p: p["producer"].update(git_sha="e" * 40), lambda p: p.update(support_probe=True),
                     lambda p: p.update(extra="private-canary"))
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as temp, mock.patch.object(runner.runtime, "read_file", side_effect=trusted_pins):
                payload = Path(temp).resolve() / "payload"
                plan = copy.deepcopy(template)
                if mutation:
                    mutation(plan)
                for name, raw in {**files, "trial.json": tau.encoded(plan)}.items():
                    path = payload / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                args = (payload, tau.digest(tau.encoded(plan)), toolkit, time.monotonic() + 5)
                if mutation is None:
                    self.assertEqual(runner.validate_plan(*args, parent_pipe=True, selected_operational=True), (plan, config))
                else:
                    with self.assertRaises(ValueError):
                        runner.validate_plan(*args, parent_pipe=True, selected_operational=True)
                with self.assertRaises(ValueError):
                    runner.validate_plan(*args, parent_pipe=True)

    def test_image_confinement_unchanged_and_selected_argument_required(self):
        args = ("owned", "owner", Path("/private/payload"), "a" * 64)
        ordinary = runner.create_command(*args, parent_pipe=True)
        selected = runner.create_command(*args, parent_pipe=True, selected_operational=True)
        self.assertEqual(selected, ordinary + ["--selected-operational"])
        for option, value in (("--network", "none"), ("--user", "65534:65534"), ("--cap-drop", "ALL"),
                              ("--log-driver", "none"), ("--pull", "never"), ("--memory", "4g")):
            self.assertEqual(selected[selected.index(option) + 1], value)
        self.assertNotIn("--gpus", selected)
        with self.assertRaises(ValueError):
            runner.create_command(*args, selected_operational=True)
        for flags, expected in (([], (False, False)), (["--parent-pipe"], (True, False)),
                                (["--parent-pipe", "--selected-operational"], (True, True))):
            self.assertEqual(worker.invocation(["worker", "a" * 64, *flags]), expected)
        for flags in (["--selected-operational"], ["--selected-operational", "--parent-pipe"],
                      ["--parent-pipe", "--support-probe"], ["--parent-pipe", "--selected-operational", "extra"]):
            with self.assertRaises(ValueError):
                worker.invocation(["worker", "a" * 64, *flags])
        for digest in ("A" * 64, "z" * 64, "a" * 63):
            with self.assertRaises(ValueError):
                worker.invocation(["worker", digest])

    def test_worker_dispatch_cannot_downgrade_or_substitute_mode(self):
        plan = fixture()[3]
        worker.validate_dispatch(plan, True, True)
        for parent, selected in ((False, False), (True, False), (False, True)):
            with self.assertRaises(ValueError):
                worker.validate_dispatch(plan, parent, selected)
        for change in (lambda p: p.update(selected_operational=False), lambda p: p.update(seconds=True),
                       lambda p: p.update(agent_prefix=True), lambda p: p.update(support_probe=True),
                       lambda p: p["files"].update({"live-binding.json": {}})):
            value = copy.deepcopy(plan)
            change(value)
            with self.assertRaises(ValueError):
                worker.validate_dispatch(value, True, True)

    def test_original_runner_and_evaluator_with_distinct_bounded_timeouts(self):
        names = ("tau2", "tau2.runner", "tau2.runner.batch", "tau2.runner.helpers", "tau2.data_model",
                 "tau2.data_model.simulation", "tau2.data_model.message", "tau2.evaluator", "tau2.evaluator.evaluator", "tau2.utils",
                 "tau2.environment", "tau2.environment.environment", "tau2.orchestrator", "tau2.orchestrator.orchestrator")
        modules = {name: types.ModuleType(name) for name in names}
        modules["tau2.environment.environment"].Environment = type("Environment", (), {})
        modules["tau2.orchestrator.orchestrator"].BaseOrchestrator = type("BaseOrchestrator", (), {"_execute_tool_calls": lambda *a: []})
        modules["tau2.data_model.message"].ToolMessage = type("ToolMessage", (), {})
        modules["tau2.data_model.message"].UserMessage = type("UserMessage", (), {})
        task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=["Synthetic assertion"]))
        tasks = modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        config = modules["tau2.data_model.simulation"].TextRunConfig = mock.Mock(side_effect=lambda **kw: types.SimpleNamespace(**kw))
        modules["tau2.evaluator.evaluator"].EvaluationType = types.SimpleNamespace(ALL_WITH_NL_ASSERTIONS="all-nl")
        logs = modules["tau2.utils"].llm_utils = types.SimpleNamespace(set_llm_log_dir=mock.Mock())
        single = modules["tau2.runner.batch"].run_single_task = mock.Mock(return_value=types.SimpleNamespace(
            termination_reason=types.SimpleNamespace(value="user_stop"), messages=[], reward_info=types.SimpleNamespace(reward=0.0)))
        manifest, selection, bridge = policy.configuration(), dict(policy.support.SELECTION), mock.Mock()
        with mock.patch.dict(sys.modules, modules), mock.patch.object(worker, "configure_upstream") as configure:
            self.assertEqual(worker.run_selected_upstream(None, manifest, selection, bridge), 0.0)
            configure.assert_called_once_with(bridge)
            tasks.assert_called_once_with("retail", task_split_name="base", task_ids=["2"], num_tasks=1)
            kwargs = config.call_args.kwargs
            self.assertEqual((kwargs["timeout"], kwargs["max_steps"], kwargs["seed"]), (900, 32, 42))
            self.assertEqual(kwargs["max_errors"], 1)
            self.assertEqual((kwargs["max_retries"], kwargs["hallucination_retries"], kwargs["max_concurrency"]), (0, 0, 1))
            self.assertFalse(kwargs["auto_review"] or kwargs["auto_resume"] or kwargs["verbose_logs"])
            self.assertIsNone(kwargs["save_to"])
            self.assertEqual(single.call_args.kwargs, {"seed": 42, "save_dir": None, "verbose_logs": False,
                "audio_debug": False, "audio_taps": False, "auto_review": False, "evaluation_type": "all-nl"})
            self.assertEqual(bridge.assertions, ["Synthetic assertion"])
            logs.set_llm_log_dir.assert_called_with(None)
            ordinary = trial.fixtures.manifest()
            worker.run_upstream(None, ordinary, trial.chosen(ordinary), bridge)
            self.assertEqual(config.call_args.kwargs["timeout"], min(180, ordinary["settings"]["limits"]["trial_seconds"]))
            single.reset_mock()
            for altered in ({**manifest, "quality_eligible": True}, {**manifest, "limits": {**manifest["limits"], "trial_seconds": 3600}}):
                with self.assertRaises(ValueError):
                    worker.run_selected_upstream(None, altered, selection, bridge)
            with self.assertRaises(ValueError):
                worker.run_selected_upstream(None, manifest, {**selection, "task_id": "1"}, bridge)
            single.assert_not_called()
            for reason, status in (("max_steps", "truncated"), ("timeout", "timeout"), ("agent_error", "protocol_error")):
                single.return_value.termination_reason.value = reason
                with self.assertRaises(worker.Abort) as caught:
                    worker.run_selected_upstream(None, manifest, selection, bridge)
                self.assertEqual(caught.exception.status, status)

    def test_fresh_entrypoint_preserves_canonical_abort_in_terminal_frame(self):
        # A second source execution has a different Abort class. Exercise main's
        # dispatch and the real pipe executor in a fresh isolated process.
        code = r'''
import json,os,runpy,sys,types
sys.path.insert(0, sys.argv[1])
import test_tau_selected_scorer as fixture
import tau_transport as transport
import tau_pipe_trial as pipe
files,toolkit,manifest,plan,pins=fixture.fixture()
raw=fixture.tau.encoded(plan)
entry=runpy.run_path(sys.argv[1]+'/tau_trial_worker.py',run_name='entry_fixture')
g=entry['main'].__globals__
assert g['Abort'] is not pipe.worker.Abort
reason=sys.argv[2]
names=('tau2','tau2.runner','tau2.runner.batch','tau2.runner.helpers','tau2.data_model',
       'tau2.data_model.simulation','tau2.data_model.message','tau2.evaluator','tau2.evaluator.evaluator','tau2.utils',
       'tau2.environment','tau2.environment.environment','tau2.orchestrator','tau2.orchestrator.orchestrator')
for name in names: sys.modules[name]=types.ModuleType(name)
sys.modules['tau2.environment.environment'].Environment=type('Environment',(),{})
sys.modules['tau2.orchestrator.orchestrator'].BaseOrchestrator=type('BaseOrchestrator',(),{'_execute_tool_calls':lambda *a:[]})
sys.modules['tau2.data_model.message'].ToolMessage=type('ToolMessage',(),{})
sys.modules['tau2.data_model.message'].UserMessage=type('UserMessage',(),{})
task=types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=['Synthetic assertion']))
sys.modules['tau2.runner.helpers'].get_tasks=lambda *a,**kw:[task]
sys.modules['tau2.data_model.simulation'].TextRunConfig=lambda **kw:types.SimpleNamespace(**kw)
sys.modules['tau2.evaluator.evaluator'].EvaluationType=types.SimpleNamespace(ALL_WITH_NL_ASSERTIONS='all-nl')
sys.modules['tau2.utils'].llm_utils=types.SimpleNamespace(set_llm_log_dir=lambda x:None)
sys.modules['tau2.runner.batch'].run_single_task=lambda *a,**kw:types.SimpleNamespace(
    termination_reason=types.SimpleNamespace(value=reason),messages=[],reward_info=types.SimpleNamespace(reward=0.0))
pipe.worker.configure_upstream=lambda bridge:None
g['configure_upstream']=lambda bridge:None
class Channel:
    def __init__(self,read,write): self.read,self.write=read,write
    def send(self,value,deadline): os.write(self.write,json.dumps(value).encode()+b'\n')
    def close(self): os.close(self.read);os.close(self.write)
class Proxy:
    def __init__(self,channel,deadline,*,selected_operational=False,customer_resolution=False):
        self.channel,self.deadline=channel,deadline
        self.finished=self.failed=False
        self.customer_resolution_mode=customer_resolution
    def finish(self,status,reward):
        self.channel.send({'status':status,'reward':reward},self.deadline)
        self.finished=True
transport.Channel,transport.WorkerProxy=Channel,Proxy
g['sys']=types.SimpleNamespace(flags=sys.flags,dont_write_bytecode=True,version_info=(3,12,13),
    platform='linux',argv=['worker',g['digest'](raw),'--parent-pipe','--selected-operational'],
    path=sys.path,modules=sys.modules,addaudithook=lambda hook:None)
g['os']=types.SimpleNamespace(**{**vars(os),'uname':lambda:types.SimpleNamespace(machine='x86_64'),
    'confstr':lambda key:'glibc 2.36'})
g['verify_tree']=lambda *args:None
def load(root,name):
    if name=='scripts/tau_preflight.py': return fixture.tau
    if name=='scripts/tau_aggregate.py': return fixture.trial.agg
    module=types.ModuleType(name)
    module.__file__=str(fixture.runner.ROOT/name)
    exec(compile(files[name],module.__file__,'exec'),module.__dict__)
    return module
g['load']=load
def read(path,limit):
    if path.name=='trial.json': return raw
    if path.name=='manifest.json': return fixture.tau.encoded(manifest)
    if str(path).endswith(fixture.tau.PINS): return fixture.tau.encoded(pins)
    return files[str(path).removeprefix('/opt/nib/')]
g['read']=read
sys.exit(g['main']())
'''
        for reason, expected in (("max_steps", "truncated"), ("timeout", "timeout"),
                                 ("agent_error", "protocol_error"), ("user_stop", "evaluated")):
            with self.subTest(reason=reason):
                result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                                         str(runner.ROOT / "scripts"), reason],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(result.stderr, b"")
                self.assertEqual(json.loads(result.stdout), {"status": expected,
                                                            "reward": 0.0 if expected == "evaluated" else None})


if __name__ == "__main__":
    unittest.main()
