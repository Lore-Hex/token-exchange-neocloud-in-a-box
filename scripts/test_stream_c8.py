# SPDX-License-Identifier: Apache-2.0
"""Bounded real child pipes and synthetic HTTP only; no GPU benchmark claim."""

import copy
from contextlib import contextmanager
import hashlib
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import deepseek_model_profile as profiles
import stream_c8 as c8
import stream_cohort as cohort
import stream_lab as binding
from test_stream_cohort import workload
from test_stream_lab import contract, report
import test_deepseek_tuning as tuning_tests
from test_evidence_history import git

ROOT = Path(__file__).absolute().parent.parent
BASE = "7d40f8916c8b76dbb6db93f020795d108d428c55"
MODEL = profiles.select("uncensored")["served_model_name"]
REAL_POPEN = subprocess.Popen


def endpoint():
    return c8.stream.identity.Endpoint(c8.stream.PORT, {"process": {
        "pid": 1234, "start_ticks": 1, "netns_inode": 2, "listener_inode": 3,
        "exe_sha256": "a" * 64, "boot_id_sha256": "b" * 64, "argv_sha256": "c" * 64}})


def selected():
    pins = {"stream/" + name + ".py": hashlib.sha256((ROOT / "scripts" / (name + ".py")).read_bytes()).hexdigest()
            for name in binding.source_names(binding.C8_PROFILE)}
    pins["stream-lab.py"] = pins["stream/stream_lab.py"]
    return {**contract(), "gpu_security": "nonconfidential", "execution_profile": "batch8-graphs",
            "model_profile": profiles.select("uncensored"), "sources": pins,
            "stream_cohort": {"schema": binding.SCHEMA, "profile": binding.C8_PROFILE}}


class ProcessTests(unittest.TestCase):
    @contextmanager
    def children(self, modes=(), *, fail_at=None, inherited=(), usage_dir=None):
        created = []
        def popen(args, **kwargs):
            self.assertEqual(args, ["/opt/nib/python", "stream-collector"])
            self.assertTrue(kwargs["close_fds"])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn("pass_fds", kwargs)
            if len(created) == fail_at:
                raise OSError("PRIVATE_CANARY")
            mode = modes[len(created)] if len(created) < len(modes) else "normal"
            command = [sys.executable, "-I", "-S", "-B", str(ROOT / "scripts/fixtures/stream_c8_child.py"),
                       mode, MODEL, json.dumps(inherited), json.dumps(endpoint().expected)]
            if usage_dir is not None:
                self.assertIn(mode, ("http-usage", "lost-synchronized"))
                command.append(str(usage_dir))
            process = REAL_POPEN(command, **kwargs)
            created.append(process)
            return process
        with mock.patch.object(c8.subprocess, "Popen", side_effect=popen):
            try:
                yield created
            finally:
                for process in created:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)
                        self.fail("collector escaped cleanup")
                    self.assertTrue(process.stdin.closed and process.stdout.closed)

    def run_cohort(self, seconds=8):
        def warmup(*_):
            time.sleep(0.01)
            return c8.meter.Sample("completed", 0.005, 0.001, 0.001, 0.004, 2, 2048, 5, None, "stop")
        with mock.patch.object(cohort, "_collect", side_effect=warmup):
            result = c8.run(endpoint(), workload(), deadline=time.monotonic() + seconds,
                            gpu_count=8, cohort_seconds=600, model=MODEL)
        with mock.patch.object(cohort, "c8", c8, create=True):
            value = {"schema": binding.RESULT_SCHEMA, "contract_sha256": "a" * 64,
                     "engine_identity_sha256": "b" * 64, "terminal_identity_verified": True, "cohort": result}
            self.assertEqual(binding.validate(value, selected(), "a" * 64, cohort, model=MODEL), result["completed"])
        return result

    def test_eight_overlapping_processes_keep_usage_and_close_owner_descriptors(self):
        # Stand-ins for owner stdin/stdout, control and engine diagnostic pipes.
        fds = [fd for _ in range(4) for fd in os.pipe()]
        inherited = []
        for fd in fds:
            os.set_inheritable(fd, True)
            info = os.fstat(fd)
            inherited.append((fd, info.st_dev, info.st_ino))
        try:
            with self.children(inherited=inherited) as children:
                result = self.run_cohort()
            self.assertEqual(len(children), 8)
            self.assertTrue(result["completed"])
            self.assertEqual(result["peak_in_flight"], 8)
            self.assertEqual(result["reserved_total_tokens"], 23040)
            self.assertEqual(result["known_output_tokens"], 45)
            self.assertEqual(result["measured"]["completed_output_tokens"], 40)
            self.assertNotIn("PRIVATE_CANARY", json.dumps(result))
            self.assertEqual(result["measured"]["gpu_count"], 8)
        finally:
            for fd in fds:
                os.close(fd)

    def test_spawn_failure_reaps_every_started_child_without_dispatch(self):
        with self.children(fail_at=3) as children:
            result = self.run_cohort()
        self.assertEqual(len(children), 3)
        self.assertEqual(result["stop_reason"], "collector-failed")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["started_requests"], 1)
        self.assertEqual(result["not_run_requests"], 8)

    def test_malformed_oversized_and_lost_reports_are_unknown_and_reaped(self):
        for mode in ("malformed", "oversized", "nested", "lost"):
            with self.subTest(mode=mode), self.children([mode] * 8):
                result = self.run_cohort()
                self.assertFalse(result["completed"])
                self.assertTrue(result["collectors_reaped"])
                self.assertGreater(result["requests_with_unknown_usage"], 0)
                self.assertNotIn("PRIVATE_CANARY", json.dumps(result))

    def test_known_failure_preserves_usage_and_never_counts_failed_throughput(self):
        with self.children(["known-failure"] * 8):
            result = self.run_cohort()
        self.assertEqual(result["stop_reason"], "request-failed")
        self.assertGreater(result["measured"]["reported_failed_output_tokens"], 0)
        self.assertEqual(result["measured"]["completed_output_tokens"], 0)
        self.assertTrue(result["collectors_reaped"])

    def test_valid_report_with_nonzero_exit_keeps_usage_but_cannot_pass(self):
        with self.children(["bad-exit"] * 8):
            result = self.run_cohort()
        self.assertEqual(result["stop_reason"], "collector-failed")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["known_output_tokens"], 45)
        self.assertTrue(all(row["exit_code"] == 7 for row in result["intervals"]))

    def test_setup_deadline_does_not_dispatch_or_leak_children(self):
        with self.children(["no-ready"] * 8), mock.patch.object(c8, "START_SECONDS", 0.3):
            result = self.run_cohort()
        self.assertFalse(result["completed"])
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["not_run_requests"], 8)

    def test_parent_interrupt_cancels_and_reaps_all_children(self):
        original, interrupted = c8.drain, False
        def drain(rows, *args):
            nonlocal interrupted
            if not interrupted and rows and all(row["dispatch"] is not None for row in rows):
                interrupted = True
                raise KeyboardInterrupt("PRIVATE_CANARY")
            return original(rows, *args)
        with self.children(["known-cancel"] * 8), mock.patch.object(c8, "drain", side_effect=drain):
            result = self.run_cohort()
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["started_requests"], 9)

    def test_interrupt_during_spawn_is_deferred_until_child_is_registered(self):
        original = c8.spawn
        def spawn():
            process = original()
            os.kill(os.getpid(), signal.SIGINT)
            return process
        with self.children() as children, mock.patch.object(c8, "spawn", side_effect=spawn):
            result = self.run_cohort()
        self.assertEqual(len(children), 1)
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["started_requests"], 1)

    def test_denied_interrupt_still_hard_kills_and_reaps(self):
        original = os.killpg
        def kill(pid, sig):
            if sig == signal.SIGINT:
                raise PermissionError("PRIVATE_CANARY")
            return original(pid, sig)
        with self.children(["stubborn"] * 8), mock.patch.object(os, "killpg", side_effect=kill):
            result = self.run_cohort(seconds=3.8)
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["requests_with_unknown_usage"], 8)

    def test_closed_pipe_during_setup_still_reaps_created_child(self):
        original = c8.spawn
        def spawn():
            process = original()
            process.stdin.close()
            return process
        with self.children() as children, mock.patch.object(c8, "spawn", side_effect=spawn):
            result = self.run_cohort()
        self.assertEqual(len(children), 1)
        self.assertEqual(result["stop_reason"], "collector-failed")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["started_requests"], 1)

    def test_expired_deadline_and_failed_warmup_never_spawn(self):
        with mock.patch.object(c8, "spawn") as spawn:
            result = self.run_cohort(seconds=-1)
            spawn.assert_not_called()
        self.assertEqual(result["started_requests"], 0)
        failed = c8.meter.Sample("timeout", 0, None, None, None, 0, None, None, None, None)
        with mock.patch.object(cohort, "_collect", return_value=failed), mock.patch.object(c8, "spawn") as spawn:
            result = c8.run(endpoint(), workload(), deadline=time.monotonic() + 8,
                            gpu_count=8, cohort_seconds=600, model=MODEL)
            spawn.assert_not_called()
        self.assertEqual(result["stop_reason"], "warmup-failed")
        self.assertEqual(result["requests_with_unknown_usage"], 1)

    def test_persistent_denial_or_pipe_error_cannot_skip_later_waits(self):
        def row(pid):
            process = mock.Mock(pid=pid)
            process.stdout.fileno.side_effect = ValueError()
            return {"process": process, "eof": False, "bad": False}
        rows = [row(1), row(2)]
        rows[0]["process"].stdin.close.side_effect = BrokenPipeError()
        rows[0]["process"].wait.side_effect = subprocess.TimeoutExpired("PRIVATE_CANARY", 0)
        with mock.patch.object(os, "killpg", side_effect=PermissionError("PRIVATE_CANARY")):
            self.assertFalse(c8.cleanup(rows, time.monotonic(), time.monotonic() - 1))
        for value in rows:
            value["process"].wait.assert_called_once_with(timeout=0)
            value["process"].stdout.close.assert_called_once()
            self.assertTrue(value["eof"])

    @contextmanager
    def http_server(self, *, hang=False, participants=8):
        self.assertIn(participants, (8, 16))
        seen, release = [], threading.Event()
        barrier = threading.Barrier(participants)
        class Server(ThreadingHTTPServer):
            # All released clients may connect before the accept thread runs.
            request_queue_size = participants
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.close_connection = True
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((self.path, body))
                try:
                    barrier.wait(5)
                    def event(choices, **extra):
                        return b"data: " + json.dumps({"model": MODEL, "choices": choices, **extra}).encode() + b"\n\n"
                    raw = b"".join((event([{"index": 0, "delta": {"content": "PRIVATE_CANARY"}, "finish_reason": None}]),
                        event([{"index": 0, "delta": {}, "finish_reason": "stop"}]),
                        event([], usage={"prompt_tokens": 2048, "completion_tokens": 5, "total_tokens": 2053}),
                        b"data: [DONE]\n\n"))
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(raw) + int(hang)))
                    self.end_headers()
                    self.wfile.write(raw)
                    self.wfile.flush()
                    if hang:
                        release.wait()
                except (threading.BrokenBarrierError, BrokenPipeError, ConnectionResetError):
                    pass
        httpd = Server((c8.stream.HOST, c8.stream.PORT), Handler)
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield seen
        finally:
            release.set()
            barrier.abort()
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)
            self.assertFalse(thread.is_alive())

    def test_real_http_collectors_check_peer_on_mainthread_and_keep_exact_request(self):
        with self.http_server() as seen, self.children(["http"] * 8):
            result = self.run_cohort()
        self.assertTrue(result["completed"])
        self.assertEqual(result["peak_in_flight"], 8)
        self.assertEqual(result["known_output_tokens"], 45)
        self.assertEqual(len(seen), 8)
        for path, body in seen:
            self.assertEqual(path, c8.stream.PATH)
            self.assertEqual(body["model"], MODEL)
            self.assertEqual(body["max_completion_tokens"], 512)
            self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertNotIn("PRIVATE_CANARY", json.dumps(result))

    def test_peer_failure_sends_no_http_and_reaps(self):
        with self.http_server() as seen, self.children(["http-peer-fail"] * 8):
            result = self.run_cohort()
        self.assertEqual(seen, [])
        self.assertFalse(result["completed"])
        self.assertTrue(result["collectors_reaped"])
        self.assertGreater(result["requests_with_unknown_usage"], 0)

    def test_deadline_after_http_usage_preserves_failed_usage(self):
        original_spawn, original_drain, original_cleanup = c8.spawn, c8.drain, c8.cleanup
        expired = cleaning = False
        with tempfile.TemporaryDirectory() as directory, self.http_server(hang=True) as seen, \
                self.children(["http-usage"] * 8, usage_dir=directory) as children:
            def spawn():
                if len(children) == 7:
                    # Exceed the old test's one-second startup/HTTP allowance.
                    time.sleep(1.1)
                return original_spawn()

            def drain(rows, *args):
                nonlocal expired
                original_drain(rows, *args)
                if not expired and not cleaning and len(children) == 8 and \
                        {path.name for path in Path(directory).iterdir()} == {str(p.pid) for p in children}:
                    self.assertTrue(all(row["dispatch"] is not None and row["packet"] is None
                                        and not row["bad"] for row in rows))
                    expired = True
                    # Exercise the real parent alarm only after every child has
                    # parsed usage; server writes alone do not prove delivery.
                    signal.raise_signal(signal.SIGALRM)

            def cleanup(*args):
                nonlocal cleaning
                cleaning = True
                return original_cleanup(*args)

            with mock.patch.object(c8, "spawn", side_effect=spawn), \
                    mock.patch.object(c8, "drain", side_effect=drain), \
                    mock.patch.object(c8, "cleanup", side_effect=cleanup):
                # Bounded failure guard, not the condition that triggers expiry.
                result = self.run_cohort(seconds=c8.stream.REQUEST_SECONDS)
        self.assertTrue(expired)
        self.assertEqual(len(seen), 8)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertFalse(result["completed"])
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["started_requests"], 9)
        self.assertEqual(result["requests_with_unknown_usage"], 0)
        self.assertEqual(result["known_output_tokens"], 45)
        self.assertEqual(result["measured"]["completed_output_tokens"], 0)
        self.assertEqual(result["measured"]["reported_failed_output_tokens"], 40)
        self.assertTrue(all(row["reported"] and row["exit_code"] == 0 for row in result["intervals"]))

    def test_deadline_kills_noncooperative_children_without_inventing_usage(self):
        with self.children(["stubborn"] * 8):
            result = self.run_cohort(seconds=3.8)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["requests_with_unknown_usage"], 8)

    def test_parent_request_limit_does_not_trust_child_alarm(self):
        with self.children(["stubborn"] * 8), mock.patch.object(c8.stream, "REQUEST_SECONDS", 0.3):
            result = self.run_cohort(seconds=10)
        self.assertEqual(result["stop_reason"], "deadline")
        self.assertTrue(result["collectors_reaped"])
        self.assertEqual(result["requests_with_unknown_usage"], 8)
        self.assertLess(result["elapsed_seconds"], 4)

    def test_warmup_interruption_after_attempt_is_unknown_not_notrun(self):
        with mock.patch.object(cohort, "_collect", side_effect=KeyboardInterrupt), mock.patch.object(c8, "spawn") as spawn:
            result = c8.run(endpoint(), workload(), deadline=time.monotonic() + 8,
                            gpu_count=8, cohort_seconds=600, model=MODEL)
            spawn.assert_not_called()
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertEqual(result["started_requests"], 1)
        self.assertEqual(result["not_run_requests"], 8)
        self.assertEqual(result["requests_with_unknown_usage"], 1)


class BindingTests(unittest.TestCase):
    def test_legacy_collector_transport_and_measurement_bytes_unchanged(self):
        for name in ("stream_cohort", "stream_transport", "stream_measurement", "tau_live_identity"):
            path = "scripts/" + name + ".py"
            original = git(ROOT, "show", BASE + ":" + path)
            self.assertEqual(git(ROOT, "show", "40dca5590ca1354946852c4ebbef131efeaf05f4:" + path), original)
            # The new explicit salt argument is checked structurally and with
            # unchanged legacy wire bytes in test_stream_scaling.
            if name != "stream_transport":
                from fixtures.stream_profile_compat import legacy_source
                current = (ROOT / path).read_bytes()
                if name == "stream_measurement":
                    current = legacy_source(current, name)
                self.assertEqual(current, original)

    def test_profile_is_explicit_and_restricted_to_reviewed_engine(self):
        binding.enabled(selected())
        for change in ({"execution_profile": "batch8-eager"}, {"execution_profile": "batch8-dspark"},
                       {"execution_profile": "tau8k-eager"}, {"gpu_security": "confidential"},
                       {"gpu_family": "B200"}, {"data_parallel_size": 1}, {"tau_owner": True},
                       {"provider_conformance": {}}, {"tokenizer_parity": False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                binding.enabled({**selected(), **change})
        self.assertEqual(binding.source_names(None), binding.NAMES)
        self.assertEqual(binding.source_names(binding.EXTENDED_PROFILE), binding.NAMES)

    def test_source_bound_optional_closure_ignores_ambient_modules_and_rejects_tamper(self):
        names = binding.source_names(binding.C8_PROFILE)
        sources = {"stream/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes() for name in names}
        pins = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
        pins["stream-lab.py"] = pins["stream/stream_lab.py"]
        with mock.patch.dict(sys.modules, {name: types.ModuleType("untrusted") for name in names}):
            loaded = binding.load(sources, pins)
            self.assertIs(loaded.c8.cohort, loaded)
            self.assertIsNot(loaded.c8, c8)
        for name in binding.C8_NAMES:
            path = "stream/" + name + ".py"
            with self.assertRaises(ValueError):
                binding.load({**sources, path: sources[path] + b"\n"}, pins)

    def test_prepared_payload_only_adds_closed_sources_not_engine_flags(self):
        tuning_tests.TuningProfiles.setUpClass()
        fixture = tuning_tests.TuningProfiles
        args = list(fixture.args)
        args[3] = {**args[3], **{"scripts/" + name + ".py": (ROOT / "scripts" / (name + ".py")).read_bytes()
                               for name in binding.C8_NAMES}}
        options = dict(tuning_tests.fixtures.PayloadProfiles.options, model_profile="uncensored",
                       execution_profile="batch8-graphs")
        host = tuning_tests.host
        old_files, old = host.make_payload(*args, **options, stream_profile=binding.EXTENDED_PROFILE)
        files, contract = host.make_payload(*args, **options, stream_profile=binding.C8_PROFILE)
        for name in ("arguments", "environment", "interpreter", "model_profile", "pack_bytes"):
            self.assertEqual(contract[name], old[name])
        self.assertEqual(set(files) - set(old_files), {"stream/" + name + ".py" for name in binding.C8_NAMES})
        self.assertLessEqual(len(contract["sources"]), 110)
        self.assertIsNotNone(binding.load(files, contract["sources"]).c8)

    def test_collector_entry_dispatches_only_after_context_and_selected_model(self):
        lab = tuning_tests.lab
        settings = selected()
        fake_sys = types.SimpleNamespace(flags=types.SimpleNamespace(isolated=1, no_site=1),
            dont_write_bytecode=True, argv=["fixed", "a" * 64, "stream-collector"])
        with mock.patch.object(lab, "sys", fake_sys), \
                mock.patch.object(lab, "lab_context", return_value=(settings, {}, [], {}, {})) as context, \
                mock.patch.object(lab, "model_identity", return_value=settings["model_profile"]) as identity, \
                mock.patch.object(lab, "STREAM_LAB", binding), mock.patch.object(lab, "STREAM_COHORT", cohort), \
                mock.patch.object(cohort, "c8", c8, create=True), mock.patch.object(c8, "child") as child, \
                mock.patch.object(lab, "application_and_request") as application:
            lab.lab_entry()
            context.assert_called_once_with("a" * 64)
            identity.assert_called_once_with(settings)
            child.assert_called_once_with(MODEL)
            application.assert_not_called()
            for args in (["fixed", "a" * 64, "stream-collector", "extra"], ["fixed", "a" * 64, "stream-collector"]):
                fake_sys.argv = args
                if len(args) == 3:
                    settings["stream_cohort"] = {"schema": binding.SCHEMA}
                with self.assertRaises(ValueError):
                    lab.lab_entry()
            self.assertEqual(child.call_count, 1)
            context.side_effect = ValueError("PRIVATE_CANARY")
            with self.assertRaises(ValueError):
                lab.lab_entry()
            self.assertEqual(child.call_count, 1)

    def test_missing_c8_source_binding_is_rejected_before_run(self):
        for name in (*binding.source_names(binding.C8_PROFILE), "missing-binding"):
            changed = selected()
            if name == "missing-binding":
                changed["sources"]["stream-lab.py"] = "f" * 64
            else:
                del changed["sources"]["stream/" + name + ".py"]
            with self.subTest(name=name), self.assertRaises(ValueError):
                binding.enabled(changed)

    def test_receipt_recomputes_overlap_wall_usage_and_reap_proof(self):
        value = report()
        row = value["cohort"]
        row.update(schema=c8.SCHEMA, model=MODEL, concurrency=8, elapsed_seconds=4,
                   intervals=[{"index": i, "start": 2, "end": 3, "reported": True, "exit_code": 0} for i in range(1, 9)],
                   peak_in_flight=8, collectors_reaped=True)
        row["measured"] = c8.meter._summarize_rows(row["samples"], scheduled=8, wall_seconds=2, gpu_count=8)
        with mock.patch.object(cohort, "c8", c8, create=True):
            self.assertTrue(binding.validate(value, selected(), "a" * 64, cohort, model=MODEL))
            for mutate in (
                lambda r: r.update(collectors_reaped=False), lambda r: r.update(peak_in_flight=7),
                lambda r: r["intervals"][0].update(start=0), lambda r: r["intervals"][0].update(end=5),
                lambda r: r["intervals"][0].update(index=2), lambda r: r["intervals"][0].update(reported=False),
                lambda r: r["intervals"][0].update(exit_code=7), lambda r: r["intervals"][0].update(exit_code=None),
                lambda r: r.update(known_output_tokens=999), lambda r: r.update(concurrency=1),
                lambda r: r.update(measured=c8.meter._summarize_rows(r["samples"], scheduled=8, wall_seconds=1, gpu_count=8)),
                lambda r: r.update(quality_accepted=True), lambda r: r.update(extra="PRIVATE_CANARY")):
                changed = copy.deepcopy(value)
                mutate(changed["cohort"])
                with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                    binding.validate(changed, selected(), "a" * 64, cohort, model=MODEL)
            value["terminal_identity_verified"] = False
            self.assertFalse(binding.validate(value, selected(), "a" * 64, cohort, model=MODEL))
            value["terminal_identity_verified"] = True
            changed = copy.deepcopy(value)
            late = changed["cohort"]
            late.update(elapsed_seconds=65,
                intervals=[{"index": i, "start": 61, "end": 63, "reported": True, "exit_code": 0} for i in range(1, 9)])
            late["measured"] = c8.meter._summarize_rows(late["samples"], scheduled=8, wall_seconds=63, gpu_count=8)
            with self.assertRaises(ValueError):
                binding.validate(changed, selected(), "a" * 64, cohort, model=MODEL)
            row.update(elapsed_seconds=12, peak_in_flight=1, completed=False, stop_reason="insufficient-overlap",
                       intervals=[{"index": i, "start": i + 1, "end": i + 2, "reported": True, "exit_code": 0}
                                  for i in range(1, 9)])
            row["measured"] = c8.meter._summarize_rows(row["samples"], scheduled=8, wall_seconds=10, gpu_count=8)
            self.assertFalse(binding.validate(value, selected(), "a" * 64, cohort, model=MODEL))
            row.update(completed=True, stop_reason="complete")
            with self.assertRaises(ValueError):
                binding.validate(value, selected(), "a" * 64, cohort, model=MODEL)


if __name__ == "__main__":
    unittest.main()
