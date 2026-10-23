# SPDX-License-Identifier: Apache-2.0
"""Synthetic private-pipe/source API tests, not a measured tokenizer result."""

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import stat
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_agent_prefix as prefix
import tau_support_probe as policy
import tau_transport as transport
import test_tau_trial as trial


def body():
    return {"model": prefix.MODEL, "messages": [{"role": "system", "content": "PRIVATE-policy"}],
            "tools": [{"type": "function", "function": {"name": "synthetic", "parameters": {"type": "object"}}}]}


class AgentPrefixTests(unittest.TestCase):
    def test_original_agent_initial_state_and_tools_without_initializing_or_stepping(self):
        names = ("tau2", "tau2.runner", "tau2.runner.helpers", "tau2.runner.build", "tau2.data_model",
                 "tau2.data_model.simulation", "tau2.utils", "tau2.utils.llm_utils")
        modules = {name: types.ModuleType(name) for name in names}
        task = object()
        modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        modules["tau2.data_model.simulation"].TextRunConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
        orchestrator = mock.Mock()
        state = types.SimpleNamespace(messages=[], system_messages=[object()])
        orchestrator.agent.get_init_state.return_value = state
        orchestrator.agent.tools = [types.SimpleNamespace(openai_schema=body()["tools"][0])]
        builder = modules["tau2.runner.build"].build_text_orchestrator = mock.Mock(return_value=orchestrator)
        convert = modules["tau2.utils.llm_utils"].to_litellm_messages = mock.Mock(return_value=body()["messages"])
        with mock.patch.dict(sys.modules, modules), mock.patch.object(trial.worker, "configure_upstream") as configure:
            self.assertEqual(prefix.materialize(trial.worker, policy, policy.configuration(), policy.SELECTION), body())
        convert.assert_called_once_with(state.system_messages)
        orchestrator.initialize.assert_not_called()
        orchestrator.step.assert_not_called()
        builder.assert_called_once()
        config = builder.call_args.args[0]
        self.assertEqual((config.task_ids, config.seed, config.num_trials, config.max_concurrency), (["2"], 42, 1, 1))
        self.assertFalse(config.auto_review or config.auto_resume or config.verbose_logs)
        self.assertIsNone(config.save_to)
        with self.assertRaises(trial.worker.Abort):
            configure.call_args.args[0].complete()

    def test_private_frame_rejects_wrong_plan_added_user_and_extra_content(self):
        packet = {"schema": prefix.SCHEMA, "plan_sha256": "a" * 64, "body": body()}
        channel = mock.Mock()
        channel.receive.return_value = packet
        self.assertEqual(prefix.receive(channel, "a" * 64, 1), body())
        channel.send.assert_called_once_with({"received_sha256": prefix.digest(prefix.encoded(packet)), "received": True}, 1)
        for change in (lambda p: p.update(plan_sha256="b" * 64), lambda p: p.update(extra="PRIVATE"),
                       lambda p: p["body"].update(model="substitute"),
                       lambda p: p["body"]["messages"].append({"role": "user", "content": "invented"})):
            channel.reset_mock()
            altered = copy.deepcopy(packet)
            change(altered)
            channel.receive.return_value = altered
            with self.assertRaises(ValueError):
                prefix.receive(channel, "a" * 64, 1)
            channel.send.assert_not_called()

    def test_real_private_pipe_success_extra_bytes_and_timeout(self):
        packet = prefix.encoded({"schema": prefix.SCHEMA, "plan_sha256": "a" * 64, "body": body()})
        source = ("import os,time\nb=" + repr(packet) + "\nos.write(1,len(b).to_bytes(4,'big')+b)\n"
                  "n=int.from_bytes(os.read(0,4),'big');os.read(0,n)\n")
        environment = {"PATH": "/usr/bin:/bin"}
        command = [sys.executable, "-I", "-S", "-B", "-c", source]
        self.assertEqual(prefix.attach(command, environment, "a" * 64, time.monotonic() + 10, transport), body())
        for suffix, timeout in (("os.write(1,b'extra')\n", 10), ("time.sleep(30)\n", 0.2)):
            with self.assertRaises((ValueError, TimeoutError, __import__("subprocess").TimeoutExpired)):
                prefix.attach(command[:-1] + [source + suffix], environment, "a" * 64,
                              time.monotonic() + timeout, transport)

    def test_worker_acknowledgement_requires_exact_body_hash_and_boolean(self):
        packet = {"schema": prefix.SCHEMA, "plan_sha256": "a" * 64, "body": body()}
        acknowledgement = {"received_sha256": prefix.digest(prefix.encoded(packet)), "received": True}
        channel = mock.Mock()
        channel.receive.return_value = acknowledgement
        prefix.send(channel, body(), "a" * 64, 1)
        channel.send.assert_called_once_with(packet, 1)
        for mutate in (lambda a: a.update(received=1), lambda a: a.update(received=False),
                       lambda a: a.update(received_sha256="b" * 64), lambda a: a.update(extra="PRIVATE")):
            altered = copy.deepcopy(acknowledgement)
            mutate(altered)
            channel.receive.return_value = altered
            with self.assertRaises(ValueError):
                prefix.send(channel, body(), "a" * 64, 1)

    def test_plan_flag_is_closed_and_default_confinement_is_reused(self):
        runner = trial.runner
        with mock.patch.object(runner.tau, "committed_identity", return_value={"git_sha": "a" * 40, "files": {}}) as identity:
            runner.identity(time.monotonic() + 5, True, support_probe=True, agent_prefix=True)
            names = [name for call in identity.call_args_list for name in call.kwargs.get("extra_files", ())]
            self.assertIn("scripts/tau_agent_prefix.py", names)
            self.assertIn("scripts/test_tau_agent_prefix.py", names)
        for options in ({"agent_prefix": True}, {"agent_prefix": 1},
                        {"agent_prefix": True, "support_probe": True, "live_local": True}):
            with self.assertRaises(ValueError):
                runner.identity(time.monotonic() + 5, True, **options)
        command = runner.create_command("owned", "owner", Path("/private/payload"), "a" * 64, parent_pipe=True)
        for option, value in (("--user", "65534:65534"), ("--network", "none"), ("--cap-drop", "ALL"),
                              ("--memory", "4g"), ("--pids-limit", "64"), ("--log-driver", "none")):
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertNotIn("--pid", command)

    def test_owner_never_publishes_body_or_passes_partial_execution_or_cleanup(self):
        owner = trial.runner.load("scripts/record-tau-agent-prefix.py")
        entry = owner.entry
        runner, runner_raw = entry.load("fake_owner_test", owner.ROOT / "scripts/tau_trial.py")
        parity, _ = entry.load("fake_parity_test", owner.ROOT / "scripts/engine_tokenizer_parity.py")
        sha = prefix.digest(prefix.encoded(body()))
        rendered = {"prefix": {"request_sha256": sha}, "plan_sha256": "b" * 64, "cleanup": "confirmed"}
        payload = {"agent-prefix.json": prefix.encoded(body())}
        contract = {"sources": {"agent-prefix.json": sha}, "source_bundle_sha256": "c" * 64,
                    "base_image": "pinned-test-image", "official_config": {"sha256": "d" * 64},
                    "measurement_sha256": "e" * 64}
        observation = {"result": "full-fit-unproven", "full_fit_proven": False}
        success = {"stage": "completed", "entry_pass": True, "cleanup": "confirmed",
                   "experiment": {"entry": {"agent_prefix": observation}}}
        socket = mock.Mock()
        socket.is_absolute.return_value = True
        socket.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
        socket.__str__ = mock.Mock(return_value="/private/fake.sock")
        for change in (None, lambda r: r.update(cleanup="completion-unknown"),
                       lambda r: r.update(entry_pass=False), lambda r: r.update(failure="bounded"),
                       "workspace-cleanup"):
            value = copy.deepcopy(success)
            if callable(change): change(value)
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                @contextmanager
                def workspace(**kwargs):
                    work = root / "work"
                    work.mkdir(mode=0o700)
                    yield str(work)
                    if change == "workspace-cleanup":
                        raise OSError("PRIVATE cleanup canary")
                with mock.patch.object(entry, "inputs", return_value=("a" * 40, {}, {
                        owner.SCRIPT: (owner.ROOT / owner.SCRIPT).read_bytes(), entry.SCRIPT: owner.ENTRY_SOURCE})), \
                        mock.patch.object(entry, "load", side_effect=lambda name, path: (runner, runner_raw)
                                          if name == "prefix_scorer_owner" else (parity, b"")), \
                        mock.patch.object(owner, "tokenizer_inputs", return_value={"tokenizer.json": b"synthetic"}), \
                        mock.patch.object(runner, "prepare", return_value=({runner.SCRIPT: runner_raw, "trial.json": b"{}"},
                            {"producer": {"git_sha": "a" * 40}})), \
                        mock.patch.object(runner, "publish"), \
                        mock.patch.object(runner, "run", return_value=(body(), rendered)) as render, \
                        mock.patch.object(entry, "prepare_files", return_value=(payload, contract)), \
                        mock.patch.object(entry, "publish_container_payload"), \
                        mock.patch.object(entry.recorder, "raw_metadata", return_value={}), \
                        mock.patch.object(entry.recorder, "offline_environment", return_value={}), \
                        mock.patch.object(entry.recorder, "CANCELLED", False), \
                        mock.patch.object(entry, "run_container", return_value=value), \
                        mock.patch.object(owner.tempfile, "TemporaryDirectory", side_effect=workspace), \
                        mock.patch.object(owner.signal, "signal"):
                    selected = "docker29-descriptor" if change is None else None
                    code = owner.execute(root / "archive", root / "wheels", root / "model", socket, root / "out",
                                         scorer_image_profile=selected)
                raw = (root / "out/result/output.log").read_bytes()
                result = json.loads(raw)
                self.assertEqual((code, result["result"]), (0, "pass") if change is None else (1, "fail"))
                self.assertNotIn(b"PRIVATE", raw)
                self.assertTrue((root / "out/pending.json").exists())
                self.assertFalse(result["full_fit_proven"] or result["generation_performed"])
                self.assertEqual(result["scorer_image_profile"], selected or "docker28-classic")
                self.assertEqual(render.call_args.kwargs["scorer_image_profile"], result["scorer_image_profile"])

    def test_image_profile_is_explicit_prefix_only_and_never_falls_back(self):
        runner, tau = trial.runner, trial.runner.tau
        for selected in (None, "docker28-classic", "docker29-descriptor"):
            for failure in (None, "other-profile", "architecture", "container", "terminal", "isolation", "cleanup"):
                with self.subTest(profile=selected, failure=failure), tempfile.TemporaryDirectory() as temporary:
                    classic = selected != "docker29-descriptor"
                    plan = {"image": runner.runtime.TARGET["image"], "platform": "linux/amd64",
                            "selection": dict(policy.SELECTION)}
                    toolkit = {"git_sha": "a" * 40, "files": {"scripts/tau_agent_prefix.py": "b" * 64}}
                    state, calls, published = {"exists": False, "inspections": 0}, [], {}
                    def capture(argv, **kwargs):
                        args = argv[5:]
                        calls.append(args)
                        if args[:2] == ["image", "inspect"]:
                            self.assertEqual(args, ["image", "inspect"] + ([] if classic else ["--platform", "linux/amd64"])
                                             + [runner.runtime.TARGET["image"]])
                            image = trial.image_inspection()
                            if classic != (failure == "other-profile"):
                                del image["Descriptor"]
                                image["Id"] = runner.PROBE_CONFIG
                            if failure == "architecture": image["Architecture"] = "arm64"
                            return tau.encoded([image])
                        if args[0] == "create":
                            state.update(exists=True, info=trial.LifecycleTests().info(args))
                            state["info"]["Config"]["OpenStdin"] = True
                            if classic:
                                state["info"]["Image"] = runner.PROBE_CONFIG
                                del state["info"]["ImageManifestDescriptor"]
                            return b"d" * 64
                        if args[0] == "inspect":
                            state["inspections"] += 1
                            row = copy.deepcopy(state["info"])
                            if failure == "container" or failure == "terminal" and state["inspections"] == 2:
                                row["Image"] = "sha256:" + "f" * 64
                            if failure == "isolation": row["Config"]["User"] = "0"
                            return tau.encoded([row])
                        if args[0] == "ps": return b"d" * 64 if state["exists"] else b""
                        if args[0] == "rm":
                            if failure == "cleanup": raise ValueError("PRIVATE-profile-error-canary")
                            state["exists"] = False
                            return b""
                        self.fail("unexpected command")
                    socket = mock.Mock()
                    socket.is_absolute.return_value = True
                    socket.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
                    private = types.SimpleNamespace(attach=mock.Mock(return_value=body()), digest=prefix.digest, encoded=prefix.encoded)
                    with mock.patch.object(runner, "identity", return_value=toolkit) as identity, \
                            mock.patch.object(runner, "validate_plan", return_value=(plan, {})) as validate, \
                            mock.patch.object(runner.shutil, "which", return_value="/fixed/docker"), \
                            mock.patch.object(runner.probe, "capture", side_effect=capture), \
                            mock.patch.object(runner, "load", return_value=private), \
                            mock.patch.object(runner, "parent_driver", return_value=types.SimpleNamespace(transport=transport)), \
                            mock.patch.object(runner, "publish_evidence", side_effect=lambda p, f: published.update(f)):
                        args = (Path("/private/payload"), "c" * 64, Path(temporary).resolve() / "out", socket)
                        if failure is None:
                            self.assertEqual(runner.run(*args, parent_pipe=True, agent_prefix=True,
                                scorer_image_profile=selected)[0], body())
                        else:
                            with self.assertRaises(ValueError):
                                runner.run(*args, parent_pipe=True, agent_prefix=True, scorer_image_profile=selected)
                    for method in (identity, validate):
                        self.assertTrue(method.call_args.kwargs["agent_prefix"] and method.call_args.kwargs["support_probe"])
                    record = json.loads(published["output.log"])
                    self.assertEqual(record["scorer_image_profile"], selected or "docker28-classic")
                    self.assertEqual(record["result"], "fail" if failure else "pass")
                    self.assertEqual(sum(call[:2] == ["image", "inspect"] for call in calls), 1)
                    for canary in (b"PRIVATE-policy", b"PRIVATE-profile-error-canary"):
                        self.assertNotIn(canary, b"".join(published.values()))
                    if failure in ("other-profile", "architecture"):
                        self.assertFalse(any(call[0] == "create" for call in calls))
                        self.assertIsNone(record["selected_image_identity"])
                    if failure in ("other-profile", "architecture", "container", "isolation"):
                        private.attach.assert_not_called()
                    if failure in ("container", "isolation"):
                        self.assertEqual(record["cleanup"], "create-completion-unknown")
                    elif failure == "cleanup":
                        self.assertEqual(record["cleanup"], "failed")
                    else:
                        self.assertEqual(record["cleanup"], "confirmed")

    def test_image_profile_rejects_unknown_nonstring_and_nonprefix_before_execution(self):
        runner = trial.runner
        for options in ({"scorer_image_profile": "docker29-descriptor"},
                        {"support_probe": {}, "scorer_image_profile": "docker28-classic"},
                        *({"agent_prefix": True, "scorer_image_profile": value} for value in ("", "auto", 1, []))):
            with mock.patch.object(runner, "identity") as identity, self.assertRaises(ValueError):
                runner.run(Path("/private/payload"), "a" * 64, Path("/private/output"), mock.Mock(),
                           parent_pipe=True, **options)
            identity.assert_not_called()

    def test_prefix_plan_cannot_be_reinterpreted_as_support_probe_or_change_source(self):
        runner, tau = trial.runner, trial.runner.tau
        task_root = "data/tau2/domains/retail/"
        tasks = [{"id": str(index), "initial_state": None, "user_tools": None,
                  "evaluation_criteria": {"nl_assertions": ["Synthetic assertion"]}} for index in range(5)]
        source = {task_root + "tasks.json": tau.encoded(tasks),
                  task_root + "split_tasks.json": tau.encoded({"base": [row["id"] for row in tasks]})}
        pins = {"files": {name: {"bytes": len(raw), "sha256": tau.digest(raw)} for name, raw in source.items()}}
        names = (runner.WORKER, "scripts/tau_agent_prefix.py", "scripts/tau_support_probe.py")
        files = {name: (runner.ROOT / name).read_bytes() for name in names}
        files.update({"manifest.json": tau.encoded(policy.configuration()), "inputs.json": b"{}\n",
                      **{"upstream/" + name: raw for name, raw in source.items()}})
        toolkit = {"git_sha": "a" * 40, "files": {name: tau.digest(files[name]) for name in names}}
        template = {"schema": "nib.tau-agent-prefix-plan/v1", "support_probe": True, "agent_prefix": True,
            "transport": "scripted-parent-pipe", "image": runner.runtime.TARGET["image"],
            "platform": runner.runtime.TARGET["platform"], "manifest_sha256": tau.digest(files["manifest.json"]),
            "selection": dict(policy.SELECTION), "environment": runner.ENVIRONMENT, "seconds": runner.SECONDS,
            "producer": {"git_sha": toolkit["git_sha"], "adapter_sha256": toolkit["files"][runner.WORKER],
                         "runtime_receipt_sha256": tau.digest(files["inputs.json"])},
            "files": {name: {"bytes": len(raw), "sha256": tau.digest(raw)} for name, raw in files.items()}}
        read = runner.runtime.read_file
        def read_fixture(path, limit, deadline):
            return tau.encoded(pins) if path == runner.ROOT / tau.PINS else read(path, limit, deadline)
        for mutate in (None, lambda p: p.update(agent_prefix=False), lambda p: p.update(agent_prefix=1),
                       lambda p: p.update(schema="nib.tau-cpu-probe-plan/v1"),
                       lambda p: p["files"]["scripts/tau_agent_prefix.py"].update(sha256="b" * 64),
                       lambda p: p["selection"].update(task_id="1"), lambda p: p.update(extra="PRIVATE")):
            with tempfile.TemporaryDirectory() as temporary, mock.patch.object(runner.runtime, "read_file", side_effect=read_fixture):
                payload = Path(temporary).resolve() / "payload"
                plan = copy.deepcopy(template)
                if mutate: mutate(plan)
                for name, raw in {**files, "trial.json": tau.encoded(plan)}.items():
                    path = payload / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                args = (payload, tau.digest(tau.encoded(plan)), toolkit, time.monotonic() + 5)
                if mutate is None:
                    self.assertEqual(runner.validate_plan(*args, parent_pipe=True, support_probe=True, agent_prefix=True),
                                     (plan, policy.configuration()))
                else:
                    with self.assertRaises((ValueError, trial.worker.Abort)):
                        runner.validate_plan(*args, parent_pipe=True, support_probe=True, agent_prefix=True)
                with self.assertRaises((ValueError, trial.worker.Abort)):
                    runner.validate_plan(*args, parent_pipe=True, support_probe=True)


if __name__ == "__main__":
    unittest.main()
