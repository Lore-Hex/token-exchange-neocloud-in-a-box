# SPDX-License-Identifier: Apache-2.0
"""Synthetic kernel/schema fixtures only. No model, GPU or runtime measurements."""

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import engine_runtime_observation as observation
import engine_local_correctness as correctness
import test_deepseek_lab as fixtures

host, lab = fixtures.host, fixtures.lab
ROOT = Path(__file__).resolve().parent.parent
SHA = hashlib.sha256(b"synthetic-contract-not-runtime-evidence").hexdigest()
EXE = b"synthetic-interpreter"


def contract():
    return {"operation": "local-correctness", "gpu_family": "B300", "runtime_observation": True,
            "interpreter": {"bytes": len(EXE), "sha256": observation.digest(EXE)}}


def sample(event="before-requests"):
    return {"event": event, **{name: observation.digest(name.encode()) for name in observation.DIGESTS},
            "exe_sha256": observation.digest(EXE), "stdio_null": True,
            "cgroup": {"memory.current": 1234, "memory.peak": 5678, "memory.max": 512 << 30,
                       "memory.swap.max": 0, "pids.current": 10, "pids.max": 1024}}


def report():
    return {"schema": observation.SCHEMA, "contract_sha256": SHA,
            "tau_admission": False, "quality_eligible": False, "samples": []}


class SchemaTests(unittest.TestCase):
    def test_two_samples_bind_same_process_and_never_admit_tau(self):
        row = report()
        for event in observation.EVENTS:
            row["samples"].append(sample(event))
            observation.validate(row, contract(), SHA)
        observation.validate(row, contract(), SHA, complete=True)
        self.assertFalse(row["tau_admission"])
        self.assertFalse(row["quality_eligible"])

    def test_partial_observations_cannot_be_complete(self):
        for samples in ([], [sample()]):
            row = {**report(), "samples": samples}
            observation.validate(row, contract(), SHA)
            with self.assertRaises(ValueError):
                observation.validate(row, contract(), SHA, complete=True)

    def test_binding_drift_reordered_duplicate_and_unknown_fields_reject(self):
        changes = [lambda r: r.update(contract_sha256="0" * 64),
                   lambda r: r.update(tau_admission=True), lambda r: r.update(quality_eligible=True),
                   lambda r: r.update(private_body="do-not-export"),
                   lambda r: r["samples"].reverse(), lambda r: r["samples"].append(sample()),
                   lambda r: r["samples"][1].update(event="before-requests"),
                   lambda r: r["samples"][1]["cgroup"].update({"memory.peak": 5677}),
                   lambda r: r["samples"][0].update(stdio_null=False)]
        for name in observation.DIGESTS:
            changes.append(lambda r, key=name: r["samples"][1].update({key: "a" * 64}))
        for change in changes:
            row = {**report(), "samples": [sample(), sample("after-requests")]}
            change(row)
            with self.subTest(change=change), self.assertRaises(ValueError):
                observation.validate(row, contract(), SHA)

    def test_exact_resource_limits_and_strict_numeric_types(self):
        for name, value in (("memory.max", 1024 << 30), ("pids.max", 2048),
                            ("memory.swap.max", 1), ("memory.peak", 1),
                            ("pids.current", True), ("memory.current", -1),
                            ("memory.peak", "max")):
            row = sample()
            row["cgroup"][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                observation.validate_sample(row, contract())
        row = sample()
        row["cgroup"]["memory.max"] = 1024 << 30
        observation.validate_sample(row, {**contract(), "data_parallel_size": 2})

    def test_only_explicit_b300_cc_correctness_admitted(self):
        for update in ({"runtime_observation": False}, {"runtime_observation": 1},
                       {"gpu_family": "B200"}, {"operation": "pre-serve"},
                       {"gpu_security": "nonconfidential"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                observation.enabled({**contract(), **update})


class KernelTests(unittest.TestCase):
    def test_short_reads_are_drained_and_overflow_or_deadline_reject(self):
        for chunks, expected in (([b"a", b"b", b""], b"ab"),
                                 ([b"a", b"bc", b""], None), ([b""], None)):
            stream = mock.MagicMock()
            stream.__enter__.return_value = stream
            stream.read.side_effect = chunks
            with mock.patch.object(observation, "open", return_value=stream, create=True):
                if expected is None:
                    with self.assertRaises(ValueError):
                        observation.read_kernel("fixed-kernel-file", 2, time.monotonic() + 5)
                else:
                    self.assertEqual(observation.read_kernel("fixed-kernel-file", 2, time.monotonic() + 5), expected)
        with mock.patch.object(observation, "open", create=True) as opened, self.assertRaises(ValueError):
            observation.read_kernel("fixed-kernel-file", 32, 0)
        opened.assert_not_called()

    def process_fixture(self):
        fields = [b"S", b"123", b"456", b"456"] + [b"0"] * 15 + [b"789"]
        values = {"/proc/456/stat": b"456 (comm has ) parentheses) " + b" ".join(fields),
                  "/proc/456/cgroup": b"0::/\n", "/proc/self/cgroup": b"0::/\n",
                  "/proc/456/cmdline": b"synthetic-private-argv\0", "/proc/sys/kernel/random/boot_id": b"synthetic-boot\n"}
        return values

    @contextmanager
    def kernel(self, values, *, namespaces=1, fd_mode=stat.S_IFCHR):
        def info(path, *args, **kwargs):
            return types.SimpleNamespace(st_uid=os.getuid(), st_ino=namespaces if str(path).startswith("/proc/456/") else 1,
                                         st_mode=fd_mode, st_rdev=3)
        with mock.patch.object(observation, "read_kernel", side_effect=lambda path, *_: values[str(path)]), \
                mock.patch.object(observation.os, "stat", side_effect=info):
            yield

    def test_process_is_same_uid_namespace_group_session_and_null_stdio(self):
        values = self.process_fixture()
        with self.kernel(values):
            row = observation.process(456, time.monotonic() + 5)
        self.assertEqual(row["start"], 789)
        self.assertEqual(row["parent"], 123)
        self.assertNotIn("synthetic-private-argv", json.dumps(row))
        for change in (lambda v: v.update({"/proc/456/stat": v["/proc/456/stat"].replace(b") S", b") Z")}),
                       lambda v: v.update({"/proc/456/stat": v["/proc/456/stat"].replace(b"123 456 456", b"123 455 456")}),
                       lambda v: v.update({"/proc/456/cgroup": b"0::/foreign\n"})):
            changed = dict(values)
            change(changed)
            with self.kernel(changed), self.assertRaises(ValueError):
                observation.process(456, time.monotonic() + 5)
        for kwargs in ({"namespaces": 2}, {"fd_mode": stat.S_IFREG}):
            with self.kernel(values, **kwargs), self.assertRaises(ValueError):
                observation.process(456, time.monotonic() + 5)

    def test_listener_requires_single_exact_loopback_owned_socket(self):
        entry = types.SimpleNamespace(path="fixed-fd")
        good = b"header\n0: 0100007F:1F40 00000000:0000 0A 0 0 0 0 0 765\n"
        for tcp, tcp6, target, succeeds in (
                (good, b"header\n", "socket:[765]", True),
                (good.replace(b"0100007F", b"00000000"), b"header\n", "socket:[765]", False),
                (good + good.splitlines()[1] + b"\n", b"header\n", "socket:[765]", False),
                (good, good, "socket:[765]", False),
                (good, b"header\n", "socket:[766]", False)):
            scan = mock.MagicMock()
            scan.__enter__.return_value = [entry]
            with mock.patch.object(observation, "read_kernel", side_effect=[tcp, tcp6]), \
                    mock.patch.object(observation.os, "scandir", return_value=scan), \
                    mock.patch.object(observation.os, "readlink", return_value=target):
                if succeeds:
                    self.assertEqual(observation.listener(456, time.monotonic() + 5), 765)
                else:
                    with self.assertRaises(ValueError):
                        observation.listener(456, time.monotonic() + 5)

    def test_observe_measures_bytes_rechecks_identity_and_requires_owned_sibling(self):
        before = {"parent": os.getpid(), "argv": observation.digest(b"private-argv")}
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "synthetic-exe"
            executable.write_bytes(EXE)
            original_open = open
            def opened(path, *args, **kwargs):
                self.assertEqual(str(path), "/proc/456/exe")
                return original_open(executable, *args, **kwargs)
            for mode in ("good", "parent", "process", "listener", "bytes", "missing-counter", "counter"):
                first, after = copy.deepcopy(before), copy.deepcopy(before)
                if mode == "parent": first["parent"] += 1
                if mode == "process": after["start"] = 1
                selected = contract()
                if mode == "bytes": selected["interpreter"]["sha256"] = "a" * 64
                values = sample()["cgroup"]
                if mode == "counter": values["memory.max"] = 1
                def read(path, *_):
                    if mode == "missing-counter": raise FileNotFoundError("PRIVATE")
                    return (str(values[Path(path).name]) + "\n").encode()
                with self.subTest(mode=mode), mock.patch.object(observation.sys, "platform", "linux"), \
                        mock.patch.object(observation.os, "getppid", return_value=os.getpid()), \
                        mock.patch.object(observation, "process", side_effect=[first, after]), \
                        mock.patch.object(observation, "listener", side_effect=[765, 766 if mode == "listener" else 765]), \
                        mock.patch.object(observation, "open", side_effect=opened, create=True), \
                        mock.patch.object(observation.os, "stat", return_value=executable.stat()), \
                        mock.patch.object(observation, "read_kernel", side_effect=read):
                    if mode == "good":
                        row = observation.observe(456, selected, SHA, "before-requests", time.monotonic() + 5)
                        self.assertEqual(row["cgroup"], values)
                        self.assertNotIn("private-argv", json.dumps(row))
                    else:
                        with self.assertRaises((ValueError, OSError)):
                            observation.observe(456, selected, SHA, "before-requests", time.monotonic() + 5)


class WiringTests(unittest.TestCase):
    def test_preparation_flag_is_explicit_and_run_cannot_override(self):
        for selector in ("--prepare-local-correctness-b300", "--prepare-local-correctness-b300-dp2"):
            with mock.patch.object(host.sys, "argv", [host.SCRIPT, selector, "/private/new", "--runtime-observation"]), \
                    mock.patch.object(host, "prepare") as prepare:
                self.assertEqual(host.main(), 0)
                self.assertEqual(prepare.call_args.kwargs, {"runtime_observation": True})
        for selector in ("--prepare", "--prepare-pre-serve-b300", "--prepare-nonconfidential-baseline-b300", "--run"):
            with mock.patch.object(host.sys, "argv", [host.SCRIPT, selector, "/private/new", "--runtime-observation"]), \
                    mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_observation_child_bounded_and_dead_engine_or_bad_sample_is_not_published(self):
        for mode in ("good", "timeout", "exit-before", "exit-after", "malformed", "drift"):
            engine = mock.Mock(pid=456)
            engine.poll.side_effect = [1 if mode == "exit-before" else None, 1 if mode == "exit-after" else None]
            row = report()
            response = sample()
            if mode == "drift": response["exe_sha256"] = "a" * 64
            raw = b"PRIVATE" if mode == "malformed" else observation.encoded(response)
            with self.subTest(mode=mode), mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(lab, "RUNTIME_OBSERVER", observation), \
                    mock.patch.object(lab, "quiet_command", return_value=raw,
                                      side_effect=subprocess.TimeoutExpired("fixed", 15) if mode == "timeout" else None) as run:
                if mode == "good":
                    lab.observe_runtime(engine, contract(), row, time.monotonic())
                    self.assertEqual(row["samples"], [response])
                    run.assert_called_once_with(["/opt/nib/python", "runtime-observation", "456", "before-requests"], 15, output=True)
                else:
                    with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                        lab.observe_runtime(engine, contract(), row, time.monotonic())
                    self.assertEqual(row["samples"], [])

    def test_observer_reserves_own_cleanup_before_engine_teardown(self):
        engine = mock.Mock()
        engine.poll.return_value = None
        with mock.patch.object(lab, "CORRECTNESS", correctness), \
                mock.patch.object(lab, "RUNTIME_OBSERVER", observation), \
                mock.patch.object(lab.time, "monotonic", return_value=1000), \
                mock.patch.object(lab, "quiet_command") as run, self.assertRaises(ValueError):
            lab.observe_runtime(engine, contract(), report(),
                1000 - lab.TOTAL_SECONDS + correctness.TEARDOWN_RESERVE + observation.SECONDS + observation.CLEANUP_SECONDS)
        run.assert_not_called()

    def test_observation_failure_still_cleans_owned_engine_and_prevents_requests(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        engine = mock.Mock(pid=99999999)
        engine.poll.return_value, engine.wait.return_value = None, 0
        for fail_at in (0, 1):
            visits = []
            def observe(engine, contract, row, started):
                visits.append(len(row["samples"]))
                if len(row["samples"]) == fail_at:
                    raise subprocess.TimeoutExpired("fixed", 15)
                row["samples"].append(sample())
            with self.subTest(fail_at=fail_at), mock.patch.object(lab, "RUNTIME_OBSERVER", observation), \
                    mock.patch.object(lab, "GPU_PROBE", fixtures.probe), \
                    mock.patch.object(lab, "CORRECTNESS", correctness), mock.patch.object(lab, "hashlib", hashlib, create=True), \
                    mock.patch.object(lab, "check_scratch_mounts"), \
                    mock.patch.object(lab, "read_source", return_value=b"synthetic contract"), \
                    mock.patch.object(correctness, "verify_sources"), \
                    mock.patch.object(correctness, "run", return_value={"all_cases_pass": True}) as requests, \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command"), \
                    mock.patch.object(lab, "observe_startup"), \
                    mock.patch.object(lab, "observe_runtime", side_effect=observe), \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine), \
                    mock.patch.object(lab.os, "killpg") as kill:
                row = lab.application_and_request({**contract(), "paths": ["/selected"]}, {},
                                                  [None, None, None, app], {"files": []}, {})
            self.assertEqual(requests.call_count, fail_at)
            self.assertFalse(row["functional_pass"])
            self.assertTrue(row["engine_stopped"])
            self.assertEqual(len(row["runtime_observation"]["samples"]), fail_at)
            kill.assert_called_once_with(engine.pid, signal.SIGTERM)


class PayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PayloadTests.setUpClass()
        cls.base = fixtures.PayloadTests

    def test_optional_source_binding_default_absence_and_unchanged_launch_bounds(self):
        base = self.base
        args = (base.builder, "1" * 40, base.bundle, base.extra, b"synthetic-binary", "local-correctness", "B300")
        files, baseline = host.make_payload(*args)
        again, same = host.make_payload(*args, runtime_observation=False)
        self.assertEqual((files, baseline), (again, same))
        self.assertNotIn("runtime-observation.py", files)
        self.assertNotIn("runtime_observation", baseline)
        extra = {**base.extra, host.OBSERVATION: (ROOT / host.OBSERVATION).read_bytes()}
        enabled_files, enabled = host.make_payload(base.builder, "1" * 40, base.bundle, extra,
            b"synthetic-binary", "local-correctness", "B300", runtime_observation=True)
        self.assertEqual(enabled["sources"]["runtime-observation.py"], observation.digest(extra[host.OBSERVATION]))
        for name in ("arguments", "environment", "interpreter", "candidate_bundle_sha256"):
            self.assertEqual(enabled[name], baseline[name])
        for name in files:
            if name.startswith("application/stages/"):
                self.assertEqual(enabled_files[name], files[name])
        config = {"config": {"Env": []}}
        with mock.patch.object(host, "read", return_value=b"synthetic fixed contract"):
            self.assertEqual(host.create_command("fixed", "owner", "/private/bundle", "/private/weights", baseline, config),
                             host.create_command("fixed", "owner", "/private/bundle", "/private/weights", enabled, config))
        self.assertEqual(enabled_files["runtime-observation.py"], extra[host.OBSERVATION])

    def test_host_rejects_changed_observer_source_before_accepting_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "payload").mkdir()
            (root / "payload/runtime-observation.py").write_bytes(b"raise AssertionError('must not execute')")
            selected = {**contract(), "sources": {"runtime-observation.py": observation.digest(b"other")}}
            with self.assertRaises(ValueError):
                host.validate_result({}, root, selected, None)

    def test_host_accepts_empty_failure_but_rejects_claimed_early_or_complete_samples(self):
        raw = observation.encoded(contract())
        selected = {**contract(), "sources": {"runtime-observation.py": observation.digest((ROOT / host.OBSERVATION).read_bytes())}}
        row = {"schema": "nib.deepseek-lab-correctness-result/v2", "status": "BLOCKED", "deployable": False,
               "confidential": False, "functional_pass": False, "stage": "hardware", "application": None,
               "hardware": None, "completion": None, "startup": None, "engine_stopped": False, "correctness": None,
               "runtime_observation": {**report(), "contract_sha256": observation.digest(raw)}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "payload").mkdir()
            (root / "payload/lab.json").write_bytes(raw)
            (root / "payload/runtime-observation.py").write_bytes((ROOT / host.OBSERVATION).read_bytes())
            host.validate_result(row, root, selected, None)
            for change in (lambda r: r["runtime_observation"].update(samples=[sample()]),
                           lambda r: r.update(stage="complete"), lambda r: r.update(functional_pass=True),
                           lambda r: r["runtime_observation"].update(contract_sha256="f" * 64)):
                bad = copy.deepcopy(row)
                change(bad)
                with self.assertRaises(ValueError):
                    host.validate_result(bad, root, selected, None)
