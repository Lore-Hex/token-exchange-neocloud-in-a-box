# SPDX-License-Identifier: Apache-2.0
"""Synthetic interfaces only; never import/install upstream packages or use Docker."""

import copy
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def module(name):
    path = ROOT / "scripts" / name
    value = types.ModuleType(path.stem)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), value.__dict__)
    return value


fixtures = module("test_tau_aggregate.py")
runner = module("tau_trial.py")
worker, tau, agg = runner.worker, runner.tau, runner.aggregate
CANARY = "private-prompt-canary-do-not-retain"


def chosen(manifest):
    return runner.select(manifest, "airline", manifest["domains"]["airline"]["task_ids"][0], 0, "candidate")


def producer():
    return {"git_sha": "a" * 40, "adapter_sha256": "b" * 64, "runtime_receipt_sha256": "c" * 64}


def response(content=CANARY):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = fixtures.manifest()
        self.now = [0.0]
        self.bridge = worker.Bridge(tau, self.manifest["settings"], "candidate",
                                    lambda *args: response(), clock=lambda: self.now[0])

    def request(self, model="nib-scripted-agent", **kwargs):
        return self.bridge.complete(model=model, messages=[{"role": "user", "content": CANARY}], **kwargs)

    def test_every_role_reserves_before_dispatch_and_uses_single_ledger(self):
        def transport(role, request, bounds):
            self.assertIsNotNone(self.bridge.budget.pending)
            self.assertGreater(self.bridge.budget.tokens, 1)
            self.assertEqual(bounds["num_retries"], 0)
            self.assertIn(CANARY, json.dumps(request))
            return response('{"results":[]}' if role == "evaluator" else CANARY)
        self.bridge.transport = transport
        for model in worker.MODEL_ROLES:
            self.request(model)
        self.assertEqual(self.bridge.counts, {"agent": 1, "simulator": 1, "evaluator": 1})
        self.assertEqual(self.bridge.budget.requests, 3)
        self.assertEqual(self.bridge.budget.tokens, 6)
        self.assertEqual(self.bridge.usage()["total_tokens"], 6)
        self.assertNotIn(CANARY, json.dumps(self.bridge.usage()))

    def test_unknown_usage_stays_reserved_and_sticky_even_if_error_caught(self):
        value = response()
        value["usage"] = None
        self.bridge.transport = lambda *args: value
        with self.assertRaises(worker.Abort) as caught:
            self.request()
        self.assertEqual(caught.exception.status, "protocol_error")
        reserved = self.bridge.budget.tokens
        self.assertGreater(reserved, 2)
        self.assertIsNone(self.bridge.usage())
        self.assertIsNotNone(self.bridge.budget.pending)
        with self.assertRaises(worker.Abort):
            self.request()
        self.assertEqual(self.bridge.budget.tokens, reserved)
        self.assertEqual(self.bridge.budget.requests, 1)

    def test_late_response_does_not_refund_unknown_consumption(self):
        def transport(*args):
            self.now[0] = self.bridge.budget.pending[4]
            return response()
        self.bridge.transport = transport
        with self.assertRaises(worker.Abort) as caught:
            self.request()
        self.assertEqual(caught.exception.status, "timeout")
        self.assertIsNone(self.bridge.usage())
        self.assertGreater(self.bridge.budget.tokens, 2)

    def test_cancel_after_dispatch_retains_reservation(self):
        def transport(*args):
            raise KeyboardInterrupt
        self.bridge.transport = transport
        with self.assertRaises(worker.Abort) as caught:
            self.request()
        self.assertEqual(caught.exception.status, "cancelled")
        self.assertIsNone(self.bridge.usage())

    def test_truncation_keeps_known_usage_and_never_counts_evaluated(self):
        value = response()
        value["choices"][0]["finish_reason"] = "length"
        self.bridge.transport = lambda *args: value
        with self.assertRaises(worker.Abort) as caught:
            self.request()
        self.assertEqual(caught.exception.status, "truncated")
        self.assertEqual(self.bridge.usage()["total_tokens"], 2)
        self.assertIsNone(self.bridge.budget.pending)

    def test_unknown_role_retries_stream_and_oversize_rejected_before_dispatch(self):
        for kwargs in ({"model": "unreviewed"}, {"num_retries": 1}, {"stream": True}):
            self.setUp()
            with mock.patch.object(self.bridge, "transport") as call:
                with self.assertRaises(worker.Abort):
                    self.request(**kwargs)
                call.assert_not_called()
        self.setUp()
        with self.assertRaises(worker.Abort):
            self.bridge.complete(model="nib-scripted-agent",
                                 messages=[{"content": CANARY * worker.MAX_REQUEST}])
        self.assertEqual(self.bridge.budget.requests, 0)

    def test_malformed_and_oversized_tool_response_does_not_escape_or_refund(self):
        cases = [response(CANARY * 30000), {"choices": [], "usage": {}}]
        for arguments in ('{"secret":', '{"a":1,"a":2}', CANARY * 4000, "[]"):
            cases.append({"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "fixed", "type": "function", "function": {"name": "tool", "arguments": arguments}}]},
                "finish_reason": "tool_calls"}], "usage": response()["usage"]})
        for value in cases:
            self.setUp()
            self.bridge.transport = lambda *args: value
            with self.assertRaises(worker.Abort) as caught:
                self.request()
            self.assertEqual(str(caught.exception), "protocol_error")
            self.assertIsNone(self.bridge.usage())

    def test_tool_roundtrip_remains_in_memory(self):
        value = response()
        value["choices"] = [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "lookup", "arguments": '{"id":"synthetic"}'}}]},
            "finish_reason": "tool_calls"}]
        self.bridge.transport = lambda *args: value
        first = self.request()
        self.assertEqual(first["choices"][0]["message"]["tool_calls"][0]["function"]["name"], "lookup")
        self.bridge.transport = lambda *args: response()
        self.bridge.complete(model="nib-scripted-agent", messages=[{"role": "tool", "content": CANARY}])
        self.assertEqual(self.bridge.budget.requests, 2)
        self.assertNotIn(CANARY, json.dumps(self.bridge.usage()))

    def test_evaluator_missing_duplicate_or_wrong_assertion_cannot_vacuously_pass(self):
        self.bridge.assertions = ["first", "second"]
        for results in ([], [{"expectedOutcome": "first", "metExpectation": True, "reasoning": CANARY}],
                        [{"expectedOutcome": "first", "metExpectation": True, "reasoning": CANARY}] * 2):
            self.setUp()
            self.bridge.assertions = ["first", "second"]
            self.bridge.transport = lambda *args: response(json.dumps({"results": results}))
            with self.assertRaises(worker.Abort):
                self.request("nib-scripted-evaluator")
            self.assertIsNone(self.bridge.usage())

    def test_request_limit_blocks_second_dispatch(self):
        self.bridge.budget.config["limits"]["requests"] = 1
        self.request()
        with mock.patch.object(self.bridge, "transport") as call:
            with self.assertRaises(worker.Abort) as caught:
                self.request()
            self.assertEqual(caught.exception.status, "budget_abort")
            call.assert_not_called()

    def test_nested_alarm_restores_existing_whole_deadline_not_extending_it(self):
        previous = signal.getsignal(signal.SIGALRM)
        with worker.alarm(time.monotonic() + 1):
            with worker.alarm(time.monotonic() + 5):
                self.assertLessEqual(signal.getitimer(signal.ITIMER_REAL)[0], 1)
            self.assertLessEqual(signal.getitimer(signal.ITIMER_REAL)[0], 1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
        self.assertIs(signal.getsignal(signal.SIGALRM), previous)
        with self.assertRaises(worker.Abort) as caught:
            with worker.alarm(time.monotonic() + 0.01):
                time.sleep(0.05)
        self.assertEqual(caught.exception.status, "timeout")
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))


class IntegrationTests(unittest.TestCase):
    def test_all_failure_denominators_and_content_free_output(self):
        manifest = fixtures.manifest()
        selection = chosen(manifest)
        for status in worker.STATUSES:
            def execute(root, frozen, selected, bridge):
                bridge.complete(model="nib-scripted-agent", messages=[{"role": "user", "content": CANARY}])
                bridge.complete(model="nib-scripted-simulator", messages=[{"role": "user", "content": CANARY}])
                if status == "evaluated":
                    return 0.0
                raise worker.Abort(status)
            report, accounting = worker.evaluate(tau, agg, manifest, selection, producer(), execute=execute)
            self.assertEqual(report["trials"][0]["status"], status)
            self.assertEqual(len(report["trials"]), 1)
            self.assertNotIn(CANARY, json.dumps([report, accounting]))
            rows = fixtures.reports(manifest)
            rows["candidate"] = report
            summary = fixtures.summarize(manifest, rows)
            self.assertEqual(summary["domains"]["airline"]["candidate"]["expected_trials"], 5)
            self.assertEqual(summary["domains"]["airline"]["candidate"]["counts"]["missing"], 4)
            self.assertFalse(summary["quality_accepted"])

    def test_caught_bridge_abort_cannot_become_success(self):
        manifest = fixtures.manifest()
        def execute(root, frozen, selected, bridge):
            bridge.transport = lambda *args: {"secret": CANARY}
            try:
                bridge.complete(model="nib-scripted-agent", messages=[{"content": CANARY}])
            except BaseException:
                pass
            return 1.0
        report, accounting = worker.evaluate(tau, agg, manifest, chosen(manifest), producer(), execute)
        self.assertEqual(report["trials"][0]["status"], "protocol_error")
        self.assertIsNone(report["trials"][0]["reward"])
        self.assertIsNone(report["trials"][0]["usage"])
        self.assertGreater(accounting["reserved_tokens"], 2)
        self.assertNotIn(CANARY, json.dumps([report, accounting]))

    def test_upstream_interface_uses_standard_runner_and_grader_with_all_logs_off(self):
        names = ("loguru", "litellm", "tau2", "tau2.utils", "tau2.runner", "tau2.runner.batch",
                 "tau2.runner.helpers", "tau2.data_model", "tau2.data_model.simulation",
                 "tau2.evaluator", "tau2.evaluator.evaluator_nl_assertions")
        modules = {name: types.ModuleType(name) for name in names}
        logger = mock.Mock()
        modules["loguru"].logger = logger
        modules["litellm"].ModelResponse = lambda **kwargs: kwargs
        llm = types.SimpleNamespace(set_llm_log_dir=mock.Mock())
        modules["tau2.utils"].llm_utils = llm
        evaluator = modules["tau2.evaluator.evaluator_nl_assertions"]
        modules["tau2.evaluator"].evaluator_nl_assertions = evaluator
        task = types.SimpleNamespace(evaluation_criteria=types.SimpleNamespace(nl_assertions=["synthetic"]))
        modules["tau2.runner.helpers"].get_tasks = mock.Mock(return_value=[task])
        modules["tau2.data_model.simulation"].TextRunConfig = lambda **kwargs: types.SimpleNamespace(**kwargs)
        called = []
        def single(config, received_task, **kwargs):
            called.append(config)
            self.assertIs(received_task, task)
            self.assertEqual(kwargs, {"seed": selection["seed"], "save_dir": None, "verbose_logs": False,
                                      "audio_debug": False, "audio_taps": False, "auto_review": False})
            self.assertEqual(config.max_retries, 0)
            self.assertEqual(config.hallucination_retries, 0)
            self.assertEqual(config.max_concurrency, 1)
            self.assertIsNone(config.save_to)
            for model in (config.llm_agent, config.llm_user, evaluator.DEFAULT_LLM_NL_ASSERTIONS):
                llm.completion(model=model, messages=[{"role": "user", "content": CANARY}], num_retries=0)
            llm._write_llm_log(CANARY, CANARY)
            return types.SimpleNamespace(reward_info=types.SimpleNamespace(reward=0.0),
                                         termination_reason=types.SimpleNamespace(value="user_stop"))
        modules["tau2.runner.batch"].run_single_task = single
        manifest = fixtures.manifest()
        selection = chosen(manifest)
        with mock.patch.dict(sys.modules, modules), mock.patch("logging.disable"):
            report, accounting = worker.evaluate(tau, agg, manifest, selection, producer())
        self.assertEqual(len(called), 1)
        self.assertEqual(accounting["role_requests"], {"agent": 1, "simulator": 1, "evaluator": 1})
        self.assertEqual(report["trials"][0]["status"], "evaluated")
        self.assertFalse(modules["litellm"].telemetry)
        self.assertEqual(llm.set_llm_log_dir.call_args_list, [mock.call(None), mock.call(None)])
        logger.remove.assert_called_once_with()
        self.assertNotIn(CANARY, json.dumps(report))

    def test_no_upstream_import_in_host_or_module_initialization(self):
        code = ("import runpy,sys; "
                f"runpy.run_path({str(ROOT / 'scripts/tau_trial.py')!r},run_name='fixture'); "
                "assert not any(k=='tau2' or k.startswith('tau2.') or k=='litellm' for k in sys.modules)")
        subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def test_external_calls_remain_denied_not_authorized_by_scripted_mode(self):
        for event in ("subprocess.Popen", "socket.connect", "socket.getaddrinfo", "os.fork", "os.system"):
            with self.assertRaises(worker.Abort):
                worker.deny_external(event, (CANARY,))
        worker.deny_external("socket.__new__", ())


class PreparationTests(unittest.TestCase):
    def test_exact_typer_overlap_only_and_no_hooks_paths_bytecode(self):
        def wheel(name, raw=b"identical"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr(name, raw)
            return stream.getvalue()
        with mock.patch.object(runner.runtime, "check_wheel"):
            files, owners = {}, {}
            runner.add_wheel(files, owners, wheel("typer/main.py"), "typer", {}, time.monotonic() + 5)
            runner.add_wheel(files, owners, wheel("typer/main.py"), "typer-slim", {}, time.monotonic() + 5)
            for package, name, raw in (("other", "typer/main.py", b"identical"),
                                       ("typer-slim", "typer/main.py", b"different")):
                with self.assertRaises(ValueError):
                    runner.add_wheel(files, owners, wheel(name, raw), package, {}, time.monotonic() + 5)
            for name in ("../escape", "test.pth", "source.pyc", "package/sitecustomize.py",
                         "sitecustomize/__init__.py", "thing.data/purelib/code.py", "usercustomize.py"):
                with self.assertRaises(ValueError):
                    runner.add_wheel({}, {}, wheel(name), "package", {}, time.monotonic() + 5)

    def test_publish_private_parent_and_cross_uid_readable_payload_no_overwrite(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            root = Path(temp).resolve() / "new"
            files = {"script.py": b"fixed", "nested/source": b"public"}
            runner.publish(root, files)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
            for path in (root / "payload", root / "payload/nested"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555)
            for name, data in files.items():
                path = root / "payload" / name
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
            with self.assertRaises(FileExistsError):
                runner.publish(root, files)
            # Restore only synthetic owned fixture directory permissions for cleanup.
            (root / "payload").chmod(0o700)
            (root / "payload/nested").chmod(0o700)

    def test_file_verification_rejects_links_fifo_changed_content_and_extra_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "fixed"
            path.write_bytes(b"fixed")
            index = {"fixed": {"bytes": 5, "sha256": tau.digest(b"fixed")}}
            worker.verify_tree(root, index, time.monotonic() + 5)
            path.write_bytes(b"wrong")
            with self.assertRaises(ValueError):
                worker.verify_tree(root, index, time.monotonic() + 5)
            path.unlink()
            path.symlink_to("unavailable")
            with self.assertRaises(OSError):
                worker.verify_tree(root, index, time.monotonic() + 5)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaises(ValueError):
                worker.verify_tree(root, index, time.monotonic() + 5)
            path.unlink()
            path.write_bytes(b"fixed")
            (root / "unlisted").write_bytes(b"extra")
            with self.assertRaises(ValueError):
                worker.verify_tree(root, index, time.monotonic() + 5)

    def test_container_command_has_no_gpu_network_ports_credentials_or_pull(self):
        args = runner.create_command("fixed", "a" * 32, Path("/private/payload"), "b" * 64, "sha256:" + "c" * 64)
        for key, value in (("--network", "none"), ("--cap-drop", "ALL"), ("--user", "65534:65534"),
                           ("--pull", "never"), ("--memory", "4g"), ("--cpus", "2"),
                           ("--log-driver", "none"), ("--runtime", "runc")):
            self.assertEqual(args[args.index(key) + 1], value)
        self.assertIn("--read-only", args)
        self.assertIn("-i", args)
        self.assertIn("PYTHON_DOTENV_DISABLED=1", args)
        self.assertIn("LITELLM_LOCAL_MODEL_COST_MAP=True", args)
        self.assertNotIn("--gpus", args)
        self.assertNotIn("--publish", args)

    def test_result_validator_rejects_content_injection_or_false_pass(self):
        manifest = fixtures.manifest()
        selection = chosen(manifest)
        def execute(root, frozen, selected, bridge):
            for model in ("nib-scripted-agent", "nib-scripted-simulator"):
                bridge.complete(model=model, messages=[{"role": "user", "content": CANARY}])
            return 0.0
        report, accounting = worker.evaluate(tau, agg, manifest, selection, producer(),
                                            execute=execute)
        plan = {"manifest_sha256": tau.digest(tau.encoded(manifest)), "selection": selection, "producer": producer()}
        value = {"schema": "nib.tau-scripted-trial/v1", "stage": "completed", "completed": True,
                 "report": report, "accounting": accounting, "quality_accepted": False, "confidential": False,
                 "live_transport": False, "token_usage": "synthetic-not-tokenized", "upstream_executed": True}
        runner.check_result(value, plan, manifest)
        for key, replacement in (("quality_accepted", True), ("token_usage", CANARY), ("completed", False)):
            bad = copy.deepcopy(value)
            bad[key] = replacement
            with self.assertRaises(ValueError):
                runner.check_result(bad, plan, manifest)
        bad = copy.deepcopy(value)
        bad["report"]["trials"][0]["content"] = CANARY
        with self.assertRaises(ValueError):
            runner.check_result(bad, plan, manifest)

    def test_evidence_exclusive_and_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "new"
            runner.publish_evidence(path, {"run.json": b"{}", "output.log": b"{}"})
            for name in ("run.json", "output.log"):
                self.assertEqual(stat.S_IMODE((path / name).stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                runner.publish_evidence(path, {"run.json": b"{}", "output.log": b"{}"})

    def test_fresh_worker_suppresses_native_output_and_exception_content(self):
        # Execute only our worker with fake upstream; no third-party code.
        code = f"""
import runpy,sys,os,types
w=runpy.run_path({str(ROOT / 'scripts/tau_trial_worker.py')!r},run_name='fixture')
def malicious_read(*a):
    os.write(1, {CANARY.encode()!r})
    os.write(2, {CANARY.encode()!r})
    raise RuntimeError({CANARY!r})
w['main'].__globals__['require']=lambda *a: None
w['main'].__globals__['read']=malicious_read
sys.argv=['fixed','a'*64]
sys.exit(w['main']())
"""
        observed = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
        self.assertNotIn(CANARY.encode(), observed.stdout + observed.stderr)
        value = json.loads(observed.stdout)
        self.assertFalse(value["completed"])
        self.assertIsNone(value["report"])
        self.assertEqual(observed.stderr, b"")


class LifecycleTests(unittest.TestCase):
    def test_authoritative_offline_schema_accepts_software_not_hardware_claim(self):
        recorder = module("record-engine-image.py")
        revision = recorder.checker.git(ROOT, "rev-parse", "HEAD").decode().strip()
        report = {"ended_utc": runner.now(), "result": "fail"}
        record = runner.evidence_record(revision, "b" * 64, runner.now(), report)
        with tempfile.TemporaryDirectory(dir="/private/tmp" if sys.platform == "darwin" else None) as temp:
            root = Path(temp).resolve()
            env = recorder.offline_environment(root / "control")
            binary, _ = recorder.build_validator(ROOT, revision, root, env)
            recorder.publish_evidence(record, report, binary, root, root / "evidence", env)
            self.assertEqual(json.loads((root / "evidence/run.json").read_bytes()), record)

    def info(self, args):
        image = "sha256:" + "c" * 64
        name = args[args.index("--name") + 1]
        label = args[args.index("--label") + 1].split("=")
        mount = args[args.index("--mount") + 1]
        return {"Name": "/" + name, "Image": image,
                "Config": {"Image": image, "Labels": {label[0]: label[1]}, "Entrypoint": ["/usr/bin/env"],
                           "Cmd": args[args.index(image) + 1:], "User": "65534:65534", "WorkingDir": "/scratch",
                           "Healthcheck": {"Test": ["NONE"]}, "Tty": False, "OpenStdin": False},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Runtime": "runc", "CapDrop": ["ALL"],
                               "Privileged": False, "IpcMode": "private", "CgroupnsMode": "private",
                               "Memory": 4 << 30, "MemorySwap": 4 << 30, "NanoCpus": 2000000000, "PidsLimit": 64,
                               "ShmSize": 16 << 20, "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
                               "SecurityOpt": ["no-new-privileges"], "Tmpfs": {
                                   "/scratch": "rw,nosuid,nodev,noexec,size=256m,mode=0700,uid=65534,gid=65534"}},
                "Mounts": [{"Type": "bind", "Source": mount.split(",")[1][4:], "Destination": "/opt/nib", "RW": False}],
                "State": {"Running": False, "OOMKilled": False, "ExitCode": 0}}

    def test_real_recorder_control_flow_fake_docker_clean_and_failed_output(self):
        for failure in (None, "attach", "output", "cleanup", "oom", "policy"):
            with self.subTest(failure=failure):
                manifest = fixtures.manifest()
                selection = chosen(manifest)
                plan = {"image": runner.runtime.TARGET["image"], "platform": "linux/amd64",
                        "manifest_sha256": tau.digest(tau.encoded(manifest)), "selection": selection,
                        "producer": producer()}
                def execute(root, frozen, selected, bridge):
                    for model in ("nib-scripted-agent", "nib-scripted-simulator"):
                        bridge.complete(model=model, messages=[{"content": CANARY}])
                    return 0.0
                report, accounting = worker.evaluate(tau, agg, manifest, selection, producer(), execute)
                result = {"schema": "nib.tau-scripted-trial/v1", "stage": "completed", "completed": True,
                          "report": report, "accounting": accounting, "quality_accepted": False, "confidential": False,
                          "live_transport": False, "token_usage": "synthetic-not-tokenized", "upstream_executed": True}
                state = {"exists": False}
                calls = []
                published = {}
                def capture(argv, *, env, timeout, limit):
                    args = argv[5:]
                    calls.append(args)
                    self.assertNotIn("API_KEY", env)
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(timeout, 300)
                    config = json.loads((Path(env["DOCKER_CONFIG"]) / "config.json").read_bytes())
                    self.assertEqual(config, {"auths": {"https://index.docker.io/v1/": {}}})
                    if args[:2] == ["image", "inspect"]:
                        return tau.encoded([{"Id": "sha256:" + "c" * 64, "Os": "linux", "Architecture": "amd64",
                                             "RepoDigests": [runner.runtime.TARGET["image"]], "Config": {}}])
                    if args[0] == "create":
                        state.update(exists=True, info=self.info(args))
                        return b"d" * 64
                    if args[0] == "inspect":
                        info = copy.deepcopy(state["info"])
                        if failure == "policy":
                            info["HostConfig"]["NetworkMode"] = "bridge"
                        if failure == "oom":
                            info["State"]["OOMKilled"] = True
                        return tau.encoded([info])
                    if args[0] == "start":
                        if failure == "attach":
                            raise ValueError(CANARY)
                        if failure == "output":
                            return tau.encoded({"secret": CANARY})
                        return tau.encoded(result)
                    if args[0] == "ps":
                        return b"d" * 64 if state["exists"] else b""
                    if args[0] == "rm":
                        if failure == "cleanup":
                            raise ValueError(CANARY)
                        state["exists"] = False
                        return b""
                    self.fail("unexpected fixed command")
                socket = mock.Mock()
                socket.is_absolute.return_value = True
                socket.stat.return_value = types.SimpleNamespace(st_mode=stat.S_IFSOCK)
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp).resolve() / "out"
                    toolkit = {"git_sha": "a" * 40, "files": {}}
                    with mock.patch.object(runner, "identity", return_value=toolkit), \
                         mock.patch.object(runner, "validate_plan", return_value=(plan, manifest)), \
                         mock.patch.object(runner.shutil, "which", return_value="/fixed/docker"), \
                         mock.patch.object(runner.probe, "capture", side_effect=capture), \
                         mock.patch.object(runner, "publish_evidence", side_effect=lambda p, f: published.update(f)), \
                         mock.patch("sys.stdout", new=io.StringIO()):
                        code = runner.run(Path("/private/payload"), "b" * 64, out, socket)
                self.assertEqual(code, 0 if failure is None else 1)
                self.assertNotIn(CANARY, b"".join(published.values()).decode())
                summary = json.loads(published["output.log"])
                envelope = json.loads(published["run.json"])
                self.assertEqual(envelope["execution"], "software")
                self.assertFalse(envelope["hardware_validated"])
                self.assertEqual(summary["cleanup_confirmed"], failure != "cleanup")
                self.assertTrue(any(args[0] == "rm" for args in calls))
                if failure == "policy":
                    self.assertFalse(any(args[0] == "start" for args in calls))
                if failure == "output":
                    self.assertIsNone(summary["diagnostic"])

    def test_inspect_rejects_every_critical_container_policy_mutation(self):
        args = runner.create_command("fixed", "a" * 32, Path("/private/payload"), "b" * 64, "sha256:" + "c" * 64)
        original = self.info(args)
        runner.check_container(original, "fixed", "a" * 32, Path("/private/payload"), "b" * 64, "sha256:" + "c" * 64)
        changes = {"NetworkMode": "host", "ReadonlyRootfs": False, "Runtime": "nvidia", "CapDrop": [],
                   "Privileged": True, "Devices": [{"PathOnHost": "/dev/x"}], "DeviceRequests": [{"Count": -1}],
                   "PortBindings": {"80/tcp": [{}]}, "Memory": 0, "MemorySwap": 0, "NanoCpus": 0,
                   "PidsLimit": 0, "LogConfig": {"Type": "json-file"}, "SecurityOpt": []}
        for key, value in changes.items():
            info = copy.deepcopy(original)
            info["HostConfig"][key] = value
            with self.assertRaises(ValueError):
                runner.check_container(info, "fixed", "a" * 32, Path("/private/payload"), "b" * 64, "sha256:" + "c" * 64)


if __name__ == "__main__":
    unittest.main()
