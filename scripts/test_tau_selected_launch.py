# SPDX-License-Identifier: Apache-2.0
"""Original-byte joins and real child pipes; no inference or hardware claims."""

import copy
from contextlib import ExitStack, contextmanager
import datetime
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import types
from unittest import mock

import test_tau_trial as trial
import test_tau_cpu_session as cpu
import test_tau_cpu_owner as cpu_owner
import test_tau_gpu_owner as gpu

launch = trial.module("record-tau-selected.py")
tau = launch.tau


def inputs():
    c, g = cpu.packet(), gpu.packet()
    c["expires_unix"] = g["expires_unix"] = int(time.time()) + 120
    ready = {"support": cpu.session.probes.encoded(c), "candidate": gpu.gpu.observer.encoded(g)}
    toolkit = {"git_sha": cpu.PRODUCER["git_sha"], "files": {
        name: cpu.PRODUCER["source_sha256"] for name in (*launch.cpu_rec.SOURCES, *launch.cpu_rec.OWNER_SOURCES)}}
    with mock.patch.object(cpu.session.identity, "source_receipt"), \
            mock.patch.object(launch.runner, "selected_policy", return_value=gpu.gpu.policy):
        frozen, expiry, held, image = launch.admit(ready, toolkit, cpu.session, gpu.gpu, gpu.contract(),
                                                   gpu.SHA, time.monotonic() + 300)
    return ready, toolkit, frozen, expiry, held, image


class SelectedLaunchTests(unittest.TestCase):
    def test_launch_failure_reaps_started_peer_and_records_no_false_success(self):
        for failure in ("second-spawn", "readiness", "scorer", "join", "post-scorer-interrupt"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                root = Path(temporary).resolve()
                ready, toolkit, frozen, expiry, held, image = inputs()
                args = types.SimpleNamespace(gpu_bundle=root / "gpu-bundle", gpu_receipt_sha256="c" * 64,
                    weights=root / "weights", source_archive=root / "upstream", wheelhouse=root / "wheels",
                    cpu_inputs=root / "cpu-inputs", output=root / "output",
                    deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                processes, observed = [], {}
                def spawn(*args, **kwargs):
                    self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
                    self.assertEqual(kwargs["env"], launch.environment())
                    self.assertTrue(kwargs["close_fds"] and kwargs["start_new_session"])
                    if failure == "second-spawn" and processes: raise OSError("PRIVATE_CANARY")
                    process = mock.Mock()
                    process.stdout.fileno.return_value = 7 + 2 * len(processes)
                    process.stdin.fileno.return_value = 8 + 2 * len(processes)
                    processes.append(process)
                    return process
                def cleanup(owned, deadline, *, abort):
                    self.assertEqual(abort, failure != "join")
                    observed.update(owned)
                    return {name: 1 if name == "candidate" else 0 for name in owned}
                def prepare(archive_path, wheelhouse, manifest_path, manifest_sha, selection, deadline,
                            *, parent_pipe, selected_operational):
                    self.assertEqual((archive_path, wheelhouse), (args.source_archive, args.wheelhouse))
                    self.assertEqual((manifest_path, manifest_sha, selection), (None, None, None))
                    self.assertTrue(parent_pipe and selected_operational)
                    return {}, {"producer": {"git_sha": toolkit["git_sha"]}}
                @contextmanager
                def alarm(deadline):
                    yield
                    if failure == "post-scorer-interrupt": raise KeyboardInterrupt
                mocked = ((launch.sys, "platform", "linux"),
                    (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                    (launch, "identity", mock.Mock(return_value=toolkit)),
                    (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": toolkit["git_sha"]}, {}, None, None))),
                    (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                    (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                    (launch.cpu_rec, "toolkit", mock.Mock()),
                    (launch, "preflight_images", mock.Mock()),
                    (launch.runner, "prepare", prepare),
                    (cpu.session.llama.worker, "alarm", alarm),
                    (launch.runner, "publish", mock.Mock()),
                    (launch.subprocess, "Popen", mock.Mock(side_effect=spawn)),
                    (launch, "read_ready", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "readiness" else None,
                                                     return_value=ready)),
                    (launch, "admit", mock.Mock(return_value=(frozen, expiry, held, image))),
                    (launch.runner, "run", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "scorer" else None,
                                                     return_value={"result": "pass"})),
                    (launch, "close_and_reap", mock.Mock(side_effect=cleanup)),
                    (launch, "final_join", mock.Mock(side_effect=ValueError("PRIVATE_CANARY") if failure == "join" else None,
                                                      return_value={"cpu": {}, "gpu": {}})))
                for obj, name, value in mocked:
                    stack.enter_context(mock.patch.object(obj, name, value))
                self.assertEqual(launch.run(args), 1)
                self.assertEqual(len(observed), 1 if failure == "second-spawn" else 2)
                report = tau.json_value((args.output / "evidence/output.log").read_bytes())
                self.assertEqual(report["result"], "fail")
                self.assertEqual(report["cleanup"], "confirmed" if failure == "post-scorer-interrupt" else "unconfirmed")
                self.assertNotIn("CANARY", repr(report))
                self.assertFalse(report["confidential"] or report["quality_eligible"] or report["live_admission"])

    def test_image_preflight_reuses_validators_and_each_consumers_daemon(self):
        calls = []
        bases = tau.json_value((launch.ROOT / "scripts/e2e/pins.json").read_bytes())
        def inspect(argv, **kwargs):
            calls.append((argv, {**kwargs, "env": dict(kwargs["env"])}))
            raw = tau.encoded([trial.image_inspection()]) if len(calls) == 1 else b"sha256:" + b"a" * 64 + b" linux/amd64\n"
            return subprocess.CompletedProcess(argv, 0, raw, b"")
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(launch.process_tools, "execute_process_group", side_effect=inspect))
            context = stack.enter_context(mock.patch.object(launch.process_tools, "local_docker_endpoint",
                                                            return_value="unix:///private/cpu-fixture.sock"))
            scorer_check = stack.enter_context(mock.patch.object(launch.runner, "check_image", wraps=launch.runner.check_image))
            cpu_check = stack.enter_context(mock.patch.object(launch.cpu_rec, "check_base_image", wraps=launch.cpu_rec.check_base_image))
            stack.enter_context(mock.patch.dict(os.environ, {"DOCKER_HOST": "tcp://untrusted.invalid:2375",
                                                            "DOCKER_CONFIG": "/private/untrusted"}))
            launch.preflight_images(time.monotonic() + 5)
        scorer_check.assert_called_once()
        self.assertEqual(cpu_check.call_count, 2)
        context.assert_called_once()
        self.assertNotIn("DOCKER_HOST", context.call_args.args[0])
        self.assertEqual(context.call_args.args[1], Path(launch.pwd.getpwuid(os.getuid()).pw_dir))
        self.assertEqual(calls[0][0], ["docker", "image", "inspect", "--platform", "linux/amd64", launch.runner.runtime.TARGET["image"]])
        self.assertEqual(calls[0][1]["env"]["DOCKER_HOST"], "unix:///var/run/docker.sock")
        for index, key in enumerate(("go_image", "python_image"), 1):
            self.assertEqual(calls[index][0], ["docker", "image", "inspect", "--format",
                                              "{{.Id}} {{.Os}}/{{.Architecture}}", bases[key]])
            self.assertEqual(calls[index][1]["env"]["DOCKER_HOST"], "unix:///private/cpu-fixture.sock")
        for index, (_, kwargs) in enumerate(calls):
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["max_output_bytes"], 65536 if index == 0 else 1024)
            self.assertEqual(kwargs["max_stderr_bytes"], 1024)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotEqual(kwargs["env"]["DOCKER_CONFIG"], "/private/untrusted")
            self.assertFalse(Path(kwargs["env"]["DOCKER_CONFIG"]).exists())

    def test_incompatible_or_missing_images_never_start_either_owner(self):
        for index in range(3):
            for failure in ("missing", "incompatible", "malformed", "stderr", "timeout"):
                with self.subTest(image=index, failure=failure), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                    root = Path(temporary).resolve()
                    args = types.SimpleNamespace(gpu_bundle=root / "bundle", gpu_receipt_sha256="c" * 64,
                        output=root / "output", source_archive=root / "source", wheelhouse=root / "wheels",
                        deadline=datetime.datetime.fromtimestamp(time.time() + 2400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                    calls = []
                    def inspect(argv, **kwargs):
                        position = len(calls)
                        calls.append(argv)
                        raw = tau.encoded([trial.image_inspection()]) if position == 0 else b"sha256:" + b"a" * 64 + b" linux/amd64\n"
                        code, stderr = 0, b""
                        if position == index:
                            if failure == "missing": code = 1
                            if failure == "stderr": stderr = b"PRIVATE_CANARY"
                            if failure == "timeout": raise subprocess.TimeoutExpired(argv, 15)
                            if failure == "malformed": raw = b"not-an-image"
                            if failure == "incompatible":
                                raw = (tau.encoded([{**trial.image_inspection(), "Descriptor": None,
                                                    "Id": launch.runner.PROBE_CONFIG}]) if index == 0 else raw.replace(b"amd64", b"arm64"))
                        return subprocess.CompletedProcess(argv, code, raw, stderr)
                    mocked = ((launch.sys, "platform", "linux"),
                        (launch.os, "uname", mock.Mock(return_value=types.SimpleNamespace(machine="x86_64"))),
                        (launch, "identity", mock.Mock(return_value={"git_sha": "a" * 40})),
                        (launch.gpu_rec, "bundle_inputs", mock.Mock(return_value=({"git_sha": "a" * 40}, {}, None, None))),
                        (launch.gpu_rec, "owner_module", mock.Mock(return_value=gpu.gpu)),
                        (launch.gpu_rec, "read", mock.Mock(return_value=b"fixed")),
                        (launch.cpu_rec, "toolkit", mock.Mock()),
                        (launch.process_tools, "execute_process_group", mock.Mock(side_effect=inspect)),
                        (launch.process_tools, "local_docker_endpoint", mock.Mock(return_value="unix:///private/cpu-fixture.sock")))
                    for obj, name, value in mocked:
                        stack.enter_context(mock.patch.object(obj, name, value))
                    owners = stack.enter_context(mock.patch.object(launch.subprocess, "Popen"))
                    prepare = stack.enter_context(mock.patch.object(launch.runner, "prepare"))
                    with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                        launch.run(args)
                    self.assertEqual(len(calls), index + 1)
                    owners.assert_not_called()
                    prepare.assert_not_called()
                    self.assertFalse(args.output.exists())

    def test_image_preflight_expired_budget_never_inspects_or_discovers_context(self):
        with mock.patch.object(launch.process_tools, "execute_process_group") as inspect, \
                mock.patch.object(launch.process_tools, "local_docker_endpoint") as context, self.assertRaises(ValueError):
            launch.preflight_images(time.monotonic() - 1)
        inspect.assert_not_called()
        context.assert_not_called()

    def test_image_preflight_rejects_late_success_before_context_discovery(self):
        result = subprocess.CompletedProcess([], 0, tau.encoded([trial.image_inspection()]), b"")
        with mock.patch.object(launch.process_tools, "execute_process_group", return_value=result) as inspect, \
                mock.patch.object(launch.process_tools, "local_docker_endpoint") as context, \
                mock.patch.object(launch.time, "monotonic", side_effect=(10, 16)), self.assertRaises(ValueError):
            launch.preflight_images(15)
        self.assertEqual(inspect.call_args.kwargs["timeout"], 5)
        context.assert_not_called()

    def test_original_ready_packets_bind_manifest_and_never_renew_expiry(self):
        ready, toolkit, frozen, expiry, held, image = inputs()
        self.assertEqual(image, cpu.IMAGE)
        self.assertEqual(set(frozen["framing"]), {"candidate", "simulator", "evaluator"})
        for name, raw in ready.items():
            self.assertEqual(frozen["owners"][name]["ready_sha256"], tau.digest(raw))
            self.assertLessEqual(expiry[name] - time.monotonic(), 120)
        self.assertEqual(held["ready_sha256"], tau.digest(ready["support"]))
        mutations = (lambda r: r.update(model="unreviewed"), lambda r: r.update(expires_unix=int(time.time()) - 1),
                     lambda r: r.update(contract_sha256="a" * 64))
        for change in mutations:
            bad = gpu.gpu.worker.decode(ready["candidate"])
            change(bad)
            with self.assertRaises((ValueError, gpu.gpu.worker.Abort)):
                launch.admit({**ready, "candidate": gpu.gpu.observer.encoded(bad)}, toolkit,
                    cpu.session, gpu.gpu, gpu.contract(), gpu.SHA, time.monotonic() + 300)

    def test_private_ready_read_and_drain_reap_real_owned_children(self):
        processes = {}
        try:
            for name, prefix in (("candidate", b"GPU "), ("support", b"CPU ")):
                script = "import sys;sys.stdout.buffer.write(%r);sys.stdout.flush();sys.stdin.buffer.read();print('fixed final')" % (prefix + b'{}\n')
                processes[name] = subprocess.Popen([sys.executable, "-I", "-S", "-c", script],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
            observed = launch.read_ready(processes, {"candidate": b"GPU ", "support": b"CPU "}, time.monotonic() + 3)
            self.assertEqual(observed, dict.fromkeys(processes, b"{}"))
            self.assertEqual(launch.close_and_reap(processes, time.monotonic() + 3), dict.fromkeys(processes, 0))
            self.assertTrue(all(p.poll() == 0 and p.stdin.closed and p.stdout.closed for p in processes.values()))
        finally:
            for process in processes.values():
                if process.poll() is None: process.kill()
                process.wait(timeout=3)

    def test_malformed_readiness_and_hanging_shutdown_are_bounded(self):
        for value in (b"wrong\n", b"CPU {}\nextra", b"CPU " + b"x" * 33000):
            process = subprocess.Popen([sys.executable, "-I", "-S", "-c",
                "import sys,time;sys.stdout.buffer.write(%r);sys.stdout.flush();time.sleep(30)" % value],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
            with self.assertRaises(ValueError):
                launch.read_ready({"support": process}, {"support": b"CPU "}, time.monotonic() + .2)
            with self.assertRaises(ValueError):
                launch.close_and_reap({"support": process}, time.monotonic() + .1)
            self.assertIsNotNone(process.poll())

    def test_abort_interrupts_startup_without_waiting_for_overall_cutoff(self):
        process = subprocess.Popen([sys.executable, "-I", "-S", "-c",
            "import signal,sys,time\ndef stop(*args): sys.exit(7)\nsignal.signal(signal.SIGINT,stop)\nprint('ready',flush=True)\ntime.sleep(120)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, start_new_session=True)
        try:
            self.assertEqual(process.stdout.readline(), b"ready\n")
            started = time.monotonic()
            self.assertEqual(launch.close_and_reap({"support": process}, started + 3600, abort=True), {"support": 7})
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(process.stdout.closed and process.stdin.closed)
        finally:
            if process.poll() is None: process.kill()
            process.wait(timeout=3)

    def test_final_join_requires_both_original_reports_and_preserves_gpu_blocked(self):
        ready, toolkit, frozen, expiry, held, image = inputs()
        finish = {"candidate": {"status": "evaluated", "completed_requests": 1},
                  "support": {"status": "evaluated", "completed_requests": 2}}
        runtime = {**cpu.result(held), "schema": "nib.tau-cpu-session/v4", "owner": finish["support"]}
        c = {"schema": "nib.tau-cpu-owner-run/v1", "git_sha": toolkit["git_sha"],
            "image_id": "sha256:" + image, "sources": toolkit["files"], "result": "pass",
            "cleanup": dict.fromkeys(("container", "image", "build_completion"), "confirmed"), "runtime": runtime}
        packet = gpu.gpu.worker.decode(ready["candidate"])
        experiment = {"tau_owner": {"ready_sha256": tau.digest(ready["candidate"]), **finish["candidate"]},
            "tokenizer_parity": packet["parity"], "runtime_observation": {"samples": [packet["sample"]]},
            "engine_stopped": True, "functional_pass": True}
        g = {"provenance": {"git_sha": toolkit["git_sha"], "receipt_sha256": "c" * 64},
            "cleanup": "confirmed", "functional_pass": True, "experiment": experiment,
            "status": "BLOCKED", "deployable": False, "confidential": False}
        scorer = {"result": "pass", "cleanup": "confirmed", "diagnostic": {"owner_finish": finish}}
        changes = (None, lambda c, g, s: c["cleanup"].update(image="unknown"),
                   lambda c, g, s: c["runtime"].update(ready_sha256="f" * 64),
                   lambda c, g, s: c["runtime"]["owner"].update(completed_requests=3),
                   lambda c, g, s: g["provenance"].update(receipt_sha256="f" * 64),
                   lambda c, g, s: g["experiment"].update(engine_stopped=False),
                   lambda c, g, s: g.update(cleanup="unconfirmed"),
                   lambda c, g, s: s.update(result="fail"))
        for change in changes:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                dc, dg, ds = copy.deepcopy(c), copy.deepcopy(g), copy.deepcopy(scorer)
                if change: change(dc, dg, ds)
                for name, report in (("cpu", dc), ("gpu", dg)):
                    directory = root / name
                    directory.mkdir()
                    documents = {"run.json": {"git_sha": toolkit["git_sha"], "result": "pass" if name == "cpu" else "fail"},
                                 "output.log": report}
                    if name == "cpu": documents.update({key: cpu.session.llama.worker.decode(value.encode())
                        for key, value in cpu.session.llama.worker.decode(ready["support"])["receipts"].items()})
                    for file, row in documents.items():
                        (directory / file).write_bytes(tau.encoded(row))
                args = (root, ready, held, image, toolkit, gpu.gpu, gpu.contract(), "c" * 64, ds,
                        {"candidate": 1, "support": 0}, time.monotonic() + 3)
                if change:
                    with self.assertRaises((ValueError, gpu.gpu.worker.Abort)): launch.final_join(*args)
                else:
                    self.assertEqual(set(launch.final_join(*args)), {"cpu", "gpu"})


if __name__ == "__main__":
    unittest.main()
