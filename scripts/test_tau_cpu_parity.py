# SPDX-License-Identifier: Apache-2.0
"""Synthetic protocol/supervisor controls; no model is loaded or served."""
import copy
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_cpu_parity as cpu
import test_tau_local_vllm as fixtures

ROOT = Path(__file__).resolve().parent.parent
rec = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/record-tau-cpu-parity.py"), run_name="tests"))


def fixture_report():
    measurement = cpu.probes.tokens([3, 7])
    process = {key: "a" * 64 if key.endswith("sha256") else 1 for key in cpu.identity.PROCESS}
    return {"schema": "nib.tau-cpu-parity/v1", "result": "pass", "stage": "complete",
            "engine_revision": cpu.llama.REVISION, "binaries": {"helper": "b" * 64, "server": "a" * 64},
            "models": [{"model": model, "weight_sha256": cpu.settings()["models"][model]["sha256"],
                "result": "pass", "ready_ms": 10, "process": dict(process), "owner_socket_checked": True,
                "cases": [{"id": name, "local": measurement, "server": measurement,
                           "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                           "result": "pass"} for name in cpu.probes.IDS]} for model in cpu.MODEL_IDS],
            "quality_eligible": False, "confidential": False, "live_admission": False, "failure": None}


class CPUParityTests(unittest.TestCase):
    def test_original_readiness_failure_preserved_without_claiming_root_cause(self):
        root = ROOT / "evidence/p2/2026-10-22-cpu-parity-readiness-failure"
        for name, sha in (("run.json", "9906438a0d5b75fbe9c1693357c6b55717d80950c083dbb44e5fab060a8bf4ec"),
                          ("output.log", "c9db8ed196397b20d4fdd704276a4d5c7a456c2f5529acdfb977530b600e71a9")):
            raw = (root / name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            row = json.loads(raw)
            self.assertEqual(row["git_sha"], "e75dca644aa891a1c762635ef67d9730fac936b7")
            self.assertEqual(row["result"], "fail")
        self.assertEqual(row["cleanup"], dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
        runtime = row["runtime"]
        self.assertEqual(runtime["stage"], "readiness")
        self.assertEqual(len(runtime["models"]), 1)
        self.assertEqual(runtime["models"][0]["cases"], [])
        self.assertFalse(runtime["models"][0]["owner_socket_checked"])
        self.assertEqual(set(runtime["binaries"]), {"helper", "server"})
        self.assertTrue(all(row[key] is False for key in ("confidential", "quality_eligible", "live_admission")))

    def test_diagnostics_filter_content_paths_and_bind_only_existing_source_lines(self):
        try:
            cpu.require(False)
        except ValueError as error:
            result = cpu.failure_diagnostics(error, -4)
        self.assertEqual(result["kind"], "rejected")
        self.assertEqual(result["server_exit"], -4)
        self.assertEqual(len(result["frames"]), 1)
        self.assertEqual(result["frames"][0]["source"], "scripts/tau_cpu_parity.py")
        cpu.validate_failure(result)
        for error, kind, number in ((OSError(13, fixtures.CANARY, fixtures.CANARY), "os-error", 13),
                                    (TimeoutError(fixtures.CANARY), "timeout", None),
                                    (RuntimeError(fixtures.CANARY), "other", None),
                                    (KeyboardInterrupt(fixtures.CANARY), "cancelled", None)):
            diagnostic = cpu.failure_diagnostics(error, None)
            self.assertEqual(diagnostic, {"kind": kind, "errno": number, "server_exit": None, "frames": []})
            self.assertNotIn(fixtures.CANARY, json.dumps(diagnostic))
        for change in (lambda r: r.update(kind=fixtures.CANARY), lambda r: r.update(errno=True),
                       lambda r: r.update(server_exit=True), lambda r: r.update(server_exit=256),
                       lambda r: r.update(frames=r["frames"] * 9),
                       lambda r: r["frames"][0].update(source="/private/" + fixtures.CANARY),
                       lambda r: r["frames"][0].update(line=100000),
                       lambda r: r["frames"][0].update(line=True)):
            invalid = copy.deepcopy(result)
            change(invalid)
            with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                cpu.validate_failure(invalid)
        report = fixture_report()
        report["failure"] = result
        with self.assertRaises(ValueError): cpu.validate(report)
        report["result"] = "fail"
        self.assertIs(cpu.validate(report), report)

    def test_readiness_failure_records_exit_before_cleanup_and_keeps_guard(self):
        pins = cpu.settings()
        archive, weights, work = mock.Mock(), [mock.Mock(), mock.Mock()], Path("/unused")
        archive.stat.return_value.st_size = 1
        for weight, model in zip(weights, cpu.MODEL_IDS):
            weight.stat.return_value.st_size = pins["models"][model]["bytes"]
        binaries = (Path("/fixed/server"), Path("/fixed/helper"))
        hashes = {archive: pins["engine"]["archive_sha256"], binaries[0]: "a" * 64, binaries[1]: "b" * 64,
                  **{weight: pins["models"][model]["sha256"] for weight, model in zip(weights, cpu.MODEL_IDS)}}
        for exit_code in (None, -4, 1):
            pilot, process = mock.Mock(), mock.Mock()
            pilot.controlled_env.return_value = {}
            pilot.file_digest.side_effect = hashes.__getitem__
            pilot.engine_command.return_value = ["fixed", "--api-key-file", "unused"]
            process.poll.return_value = exit_code
            def cleanup(child):
                self.assertIs(child, process)
                process.poll.return_value = -15
                return True
            pilot.stop.side_effect = cleanup
            with mock.patch.object(cpu, "confinement"), mock.patch.object(cpu, "tools", return_value=(pilot, None)), \
                    mock.patch.object(cpu, "build", return_value=binaries), mock.patch.object(cpu, "native_tokens"), \
                    mock.patch.object(cpu.subprocess, "Popen", return_value=process), \
                    mock.patch.object(cpu, "endpoint", side_effect=PermissionError(13, fixtures.CANARY)) as guard:
                report = cpu.run(archive, weights, work)
            guard.assert_called_once()
            pilot.stop.assert_called_once_with(process)
            self.assertEqual(cpu.validate(report)["stage"], "readiness")
            self.assertEqual(report["failure"]["server_exit"], exit_code)
            self.assertEqual(report["failure"]["errno"], 13)
            self.assertEqual(report["models"][0]["cases"], [])
            self.assertNotIn(fixtures.CANARY, json.dumps(report))

    def test_same_fixed_probes_and_no_semantic_repair(self):
        source = ROOT / "scripts/engine_tokenizer_parity.py"
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         "09d704cf6d87c23b4115d40d501248a74502b37124cff57da5ad59ac7ff9951a")
        for model in cpu.MODEL_IDS:
            for probe in cpu.probes.probes():
                original = copy.deepcopy(probe)
                body = cpu.body(model, probe)
                self.assertEqual(body["messages"], original["messages"])
                self.assertEqual(body.get("tools"), original.get("tools"))
                self.assertEqual(body["max_completion_tokens"], 1)
                self.assertEqual(body["reasoning_effort"], "none")
                self.assertEqual(probe, original)

    def test_four_comparisons_preserve_partial_failure_and_suppress_generation(self):
        local = [cpu.probes.tokens([3, 7])] * 4
        client = mock.Mock()
        client.measure.return_value = local[0]
        client.complete.return_value = {"usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        rows = []
        cpu.compare(client, cpu.MODEL_IDS[0], local, time.monotonic() + 10, rows)
        self.assertEqual([row["id"] for row in rows], list(cpu.probes.IDS))
        self.assertTrue(all(row["result"] == "pass" for row in rows))
        self.assertEqual(client.complete.call_count, 4)
        client.reset_mock()
        client.measure.return_value = cpu.probes.tokens([7, 3])
        rows = []
        cpu.compare(client, cpu.MODEL_IDS[0], local, time.monotonic() + 10, rows)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"], "fail")
        client.complete.assert_not_called()
        client.measure.side_effect = ValueError(fixtures.CANARY)
        rows = []
        with self.assertRaises(ValueError):
            cpu.compare(client, cpu.MODEL_IDS[0], local, time.monotonic() + 10, rows)
        self.assertEqual(rows[0]["server"], None)
        self.assertNotIn(fixtures.CANARY, json.dumps(rows))

    def test_usage_mismatch_boolean_and_overrun_cannot_pass(self):
        for usage in ({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                      {"prompt_tokens": 2, "completion_tokens": True, "total_tokens": 3},
                      {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
                      {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 4}):
            client = mock.Mock()
            client.measure.return_value = cpu.probes.tokens([3, 7])
            client.complete.return_value = {"usage": usage}
            rows = []
            with self.assertRaises(ValueError):
                cpu.compare(client, cpu.MODEL_IDS[0], [client.measure.return_value] * 4,
                            time.monotonic() + 5, rows)
            self.assertEqual(rows[0]["result"], "fail")
            self.assertIsNone(rows[0]["usage"])

    def test_http_ids_transient_and_each_connection_owned(self):
        owner = mock.Mock()
        with fixtures.endpoint([{"prompt": fixtures.CANARY}, {"tokens": [3, 7]}]) as (port, seen):
            client = cpu.MeasuredClient(port, cpu.MODEL_IDS[0], owner)
            result = client.measure(cpu.body(cpu.MODEL_IDS[0], cpu.probes.probes()[0]), time.monotonic() + 2)
        self.assertEqual(result, cpu.probes.tokens([3, 7]))
        self.assertEqual(owner.check.call_count, 4)
        self.assertIsNone(client.ids)
        self.assertEqual(client.attempted, 2)
        self.assertEqual([row[0] for row in seen], ["/apply-template", "/tokenize"])

    def test_strict_report_rejects_content_and_incomplete_passes(self):
        self.assertEqual(cpu.validate(fixture_report())["result"], "pass")
        for change in (lambda r: r.update(extra=fixtures.CANARY),
                       lambda r: r.update(live_admission=True),
                       lambda r: r.update(models=r["models"][:1]),
                       lambda r: r["models"][0].update(owner_socket_checked=False),
                       lambda r: r["models"][0].update(process=None),
                       lambda r: r["models"][0].update(cases=[]),
                       lambda r: r["models"][0]["cases"][0].update(server=cpu.probes.tokens([7, 3])),
                       lambda r: r["models"][0]["cases"][0]["usage"].update(prompt_tokens=True),
                       lambda r: r["models"][0]["process"].update(exe_sha256="c" * 64)):
            report = fixture_report()
            change(report)
            with self.assertRaises((ValueError, cpu.llama.worker.Abort)):
                cpu.validate(report)
        failed = fixture_report()
        failed.update(result="fail", stage="comparison")
        failed["models"][0]["result"] = "fail"
        failed["models"][0]["cases"][0].update(result="fail", server=None, usage=None)
        self.assertIs(cpu.validate(failed), failed)

    def test_owned_endpoint_rejects_mismatched_listener_and_argv(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        deadline = time.monotonic() + 2
        for rows, facts in ([(('00000000:1F40', '00000000:0000', '0A', 9),), {}],
                            [(('0100007F:1F40', '00000000:0000', '0A', 9),), {"argv_sha256": "bad"}]):
            with mock.patch.object(cpu.identity, "tcp_rows", return_value=rows), \
                    mock.patch.object(cpu.identity, "process_facts", return_value=facts), self.assertRaises(ValueError):
                cpu.endpoint(process, 8000, "a" * 64, ["fixed"], deadline)

    def test_exact_confinement_no_root_caps_or_network_fallback(self):
        values = {"/proc/self/status": b"CapInh:\t0\nCapPrm:\t0\nCapEff:\t0\nCapBnd:\t0\nCapAmb:\t0\nNoNewPrivs:\t1\nSeccomp:\t2\n",
                  "/sys/fs/cgroup/memory.max": b"8589934592\n", "/sys/fs/cgroup/memory.swap.max": b"0\n",
                  "/sys/fs/cgroup/pids.max": b"128\n", "/sys/fs/cgroup/cpu.max": b"400000 100000\n"}
        with mock.patch.object(cpu.sys, "platform", "linux"), \
                mock.patch.multiple(cpu.os, getuid=lambda: 65532, geteuid=lambda: 65532,
                                    getgid=lambda: 65532, getegid=lambda: 65532), \
                mock.patch.object(cpu.os, "listdir", return_value=[]), \
                mock.patch.object(cpu.socket, "if_nameindex", return_value=[(1, "lo")]), \
                mock.patch.object(cpu.identity, "proc_read", side_effect=lambda path, limit: values[path]):
            cpu.confinement()
            for key in values:
                before = values[key]
                values[key] = before.replace(b"CapEff:\t0", b"CapEff:\t1") if key.endswith("status") else b"max\n"
                with self.subTest(key=key), self.assertRaises(ValueError):
                    cpu.confinement()
                values[key] = before
            with mock.patch.object(cpu.socket, "if_nameindex", return_value=[(1, "lo"), (2, "eth0")]), self.assertRaises(ValueError):
                cpu.confinement()

    def test_preflight_failure_has_no_exception_text_or_child_execution(self):
        with mock.patch.object(cpu, "confinement", side_effect=ValueError(fixtures.CANARY)), \
                mock.patch.object(cpu, "tools") as tools:
            report = cpu.run(Path("/unused"), [], Path("/unused"))
        self.assertEqual(cpu.validate(report)["result"], "fail")
        self.assertNotIn(fixtures.CANARY, json.dumps(report))
        tools.assert_not_called()

    def test_helper_api_logging_and_no_independent_http_text(self):
        source = (ROOT / "scripts/tau_cpu_parity.cpp").read_text()
        for required in ('params.vocab_only = true', 'params.n_gpu_layers = 0',
                         'common_chat_templates_init(model.get(), "")',
                         'common_chat_msgs_parse_oaicompat(probe.at("messages"))',
                         'common_chat_tools_parse_oaicompat', 'common_chat_session session',
                         'common_tokenize(vocab, session.prompt().str(), true, true)',
                         'COMMON_REASONING_FORMAT_DEEPSEEK', 'inputs.enable_thinking = false',
                         'dup2(sink, STDERR_FILENO)', 'S_ISFIFO(input_info.st_mode)',
                         'tokens.size() <= 1024', 'raw.size() <= 65536'):
            self.assertIn(required, source)
        self.assertNotIn('/apply-template', source)
        self.assertNotIn('what()', source)
        self.assertNotIn('Reply with', source)

    def test_container_has_no_network_mounts_devices_or_unbounded_outputs(self):
        args = rec.runtime_command("sha256:" + "a" * 64, "nib-test")
        for flag, value in (("--network", "none"), ("--pull", "never"), ("--memory", "8g"),
                            ("--memory-swap", "8g"), ("--cpus", "4"), ("--pids-limit", "128"),
                            ("--cap-drop", "ALL"), ("--user", "65532:65532"), ("--log-driver", "none")):
            self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--read-only", args)
        for forbidden in ("--gpus", "--device", "--privileged", "--volume", "--mount", "--publish"):
            self.assertNotIn(forbidden, args)
        dockerfile = (ROOT / "scripts/tau_cpu_parity.Dockerfile").read_text()
        self.assertIn('--no-index', dockerfile)
        self.assertIn('--require-hashes', dockerfile)
        self.assertNotIn('apt-get', dockerfile)

    def test_publication_is_exclusive_nofollow_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                rec.write(fd, "run.json", {"result": "fail"})
                with self.assertRaises(FileExistsError):
                    rec.write(fd, "run.json", {})
                with self.assertRaises(ValueError):
                    rec.write(fd, "large", {"x": "x" * 65536})
                (Path(directory) / "link").symlink_to("run.json")
                with self.assertRaises(OSError):
                    rec.write(fd, "link", {})
                self.assertEqual((Path(directory) / "run.json").stat().st_mode & 0o777, 0o600)
            finally:
                os.close(fd)

    def test_native_child_bounded_json_and_timeout_reaped(self):
        pilot, snapshot = cpu.tools()
        for code, expected in (("import sys;sys.stdin.buffer.read();sys.stdout.write('bad')", ValueError),
                               ("import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*70000)", ValueError),
                               ("import sys,time;sys.stdin.buffer.read();time.sleep(5)", subprocess.TimeoutExpired)):
            real = subprocess.Popen
            children = []
            def spawn(*args, **kwargs):
                child = real([sys.executable, "-I", "-S", "-B", "-c", code], **kwargs)
                children.append(child)
                return child
            with mock.patch.object(cpu.subprocess, "Popen", side_effect=spawn), self.assertRaises(expected):
                cpu.native_tokens(Path("/unused"), Path("/unused"), {"PATH": "/usr/bin:/bin"},
                                  snapshot, time.monotonic() + 0.15)
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(all(stream.closed for stream in (children[0].stdin, children[0].stdout, children[0].stderr)))

    def test_source_closure_tampering_rejects_before_import_or_model_execution(self):
        namespace = rec.worker.__globals__
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = {}
            for name in rec.SOURCES:
                raw = (ROOT / name).read_bytes()
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                index[name] = rec.digest(raw)
            self.assertEqual(index[rec.SNAPSHOT], rec.SNAPSHOT_SHA)
            (root / 'source.json').write_text(json.dumps(index))
            for name in rec.SOURCES:
                target = root / name
                original = target.read_bytes()
                target.write_bytes(original + b'\n')
                importer = mock.Mock(side_effect=AssertionError('imports must not run'))
                with mock.patch.dict(namespace, ROOT=root, toolkit=importer), self.assertRaises(ValueError):
                    rec.worker()
                importer.assert_not_called()
                target.write_bytes(original)

    def test_existing_private_input_copy_checks_bytes_and_rejects_links(self):
        pilot, _ = cpu.tools()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'input'
            source.write_bytes(b'fixed public fixture')
            sha = rec.digest(source.read_bytes())
            pilot.checked_copy(source, root / 'copy', sha, 20)
            self.assertEqual(pilot.file_digest(root / 'copy'), sha)
            with self.assertRaises(pilot.PilotError):
                pilot.checked_copy(source, root / 'wrong', '0' * 64, 20)
            with self.assertRaises(pilot.PilotError):
                pilot.checked_copy(source, root / 'large', sha, 1)
            (root / 'link').symlink_to(source)
            with self.assertRaises(OSError):
                pilot.checked_copy(root / 'link', root / 'linked', sha, 20)

    def test_supervisor_timeout_cleanup_and_untrusted_child_output(self):
        namespace = rec.execute.__globals__
        for failure in (None, "timeout", "content", "cleanup", "build"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                snapshot = mock.Mock()
                snapshot.local_docker_endpoint.return_value = "unix:///fixture"
                def call(args, **kwargs):
                    if args[1:3] == ["image", "inspect"]:
                        self.assertNotIn("--platform", args)
                        return subprocess.CompletedProcess(args, 0, b"sha256:" + b"a" * 64 + b" linux/amd64\n", b"")
                    if args[1] == "build":
                        self.assertEqual(args[args.index("--platform") + 1], "linux/amd64")
                        if failure == "build":
                            raise subprocess.TimeoutExpired(args, 240)
                        (work / "image-id").write_text("sha256:" + "a" * 64)
                        return subprocess.CompletedProcess(args, 0, None, None)
                    self.assertEqual(kwargs["timeout"], 1800)
                    self.assertEqual(kwargs["max_output_bytes"], 65536)
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(args, 1800, output=fixtures.CANARY.encode())
                    raw = cpu.probes.encoded(fixture_report()) if failure != "content" else fixtures.CANARY.encode()
                    return subprocess.CompletedProcess(args, 0, raw, b"")
                snapshot.execute_process_group.side_effect = call
                cleaned = mock.Mock(return_value={"result": "fail" if failure == "cleanup" else "pass",
                    "container": {"absence": "unknown" if failure == "cleanup" else "confirmed"},
                    "image": {"absence": "confirmed"}})
                report = {"result": "fail", "runtime": None}
                with mock.patch.dict(namespace, toolkit=lambda _: cpu,
                        context=lambda *args: ("b" * 64, {}), load=lambda *args: types.SimpleNamespace(cleanup_resources=cleaned)):
                    rec.execute(ROOT, work, {}, snapshot, [], report, Path("/unused"))
                cleaned.assert_called_once()
                self.assertEqual(report["result"], "pass" if failure is None else "fail")
                self.assertNotIn(fixtures.CANARY, json.dumps(report))
                if failure == "build":
                    self.assertEqual(report["cleanup"]["build_completion"], "unknown")


if __name__ == "__main__":
    unittest.main()
