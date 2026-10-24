# SPDX-License-Identifier: Apache-2.0
"""Synthetic endpoints and actual private pipes; no model-quality claim."""

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import tau_cpu_owner as adapter
import test_tau_cpu_session as fixtures
import test_tau_owner_channel as rpc_fixtures

session, rpc, tau = adapter.session, adapter.owners, adapter.tau


class Measured:
    def __init__(self, port, model, endpoint):
        self.port, self.model, self.endpoint, self.ids = port, model, endpoint, None

    def count(self, body, context, deadline):
        return 7

    def complete(self, body, count, deadline):
        return rpc_fixtures.completion()


def request(role):
    result = rpc_fixtures.body(role)
    result["model"] = adapter.policy.configuration()["support"][role]["model"]
    return result


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=3)


class CPUOwnerTests(unittest.TestCase):
    def test_source_closure_loads_without_ambient_imports(self):
        root = Path(__file__).resolve().parent.parent
        script = ("import pathlib,types,sys; r=pathlib.Path(" + repr(str(root)) + "); "
                  "p=r/'scripts/record-tau-cpu-parity.py'; m=types.ModuleType('record'); m.__file__=str(p); "
                  "exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__); "
                  "assert all((r/n).is_file() for n in m.SOURCES+m.OWNER_SOURCES); m.toolkit(r,selected_owner=True); "
                  "assert sys.modules['tau_cpu_owner'].session is sys.modules['tau_cpu_session']; "
                  "assert sys.modules['tau_owner_channel'].cpu is sys.modules['tau_cpu_parity']")
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", script], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_finish_summary_is_exact_and_cannot_promote_partial_session(self):
        held = {"ready_sha256": "a" * 64, "receipts": dict.fromkeys(session.FILES, "b" * 64)}
        original = {**fixtures.result(held), "schema": "nib.tau-cpu-session/v4",
                    "owner": {"status": "evaluated", "completed_requests": 2}}
        self.assertEqual(session.validate(original), original)
        changes = (lambda r: r.update(owner=None), lambda r: r["owner"].update(completed_requests=0),
                   lambda r: r["owner"].update(completed_requests=True), lambda r: r["owner"].update(completed_requests=33),
                   lambda r: r["owner"].update(status="timeout"), lambda r: r["owner"].update(content="private"),
                   lambda r: r.update(acknowledged=False), lambda r: r.update(cleanup="unknown"),
                   lambda r: r.update(schema="nib.tau-cpu-session/v2"), lambda r: r.update(live_admission=True))
        for change in changes:
            value = copy.deepcopy(original)
            change(value)
            with self.assertRaises((ValueError, rpc.worker.Abort)):
                session.validate(value)
        failed = {**original, "result": "fail", "owner": {"status": "budget_abort", "completed_requests": 0}}
        self.assertEqual(session.validate(failed), failed)

    def test_binding_rejects_wrong_role_model(self):
        value = fixtures.packet()
        raw = session.probes.encoded(value)
        self.assertEqual(adapter.binding(raw)["ready_sha256"], tau.digest(raw))
        row = rpc.worker.decode(value["receipts"]["simulator-runtime.json"].encode())
        row["model"] = "wrong"
        value["receipts"]["simulator-runtime.json"] = tau.encoded(row).decode()
        with self.assertRaises(rpc.worker.Abort):
            adapter.binding(session.probes.encoded(value))

    def test_owned_pair_roundtrip_finish_and_original_readiness(self):
        processes = [subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(10)"],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                     for _ in session.ROLES]
        try:
            value, endpoints = fixtures.packet(), []
            for role, process in zip(session.ROLES, processes):
                row = rpc.worker.decode(value["receipts"][role + "-runtime.json"].encode())
                row["process"]["pid"] = process.pid
                value["receipts"][role + "-runtime.json"] = tau.encoded(row).decode()
                token = rpc.worker.decode(value["receipts"][role + "-tokenizer.json"].encode())
                token["runtime_receipt_sha256"] = tau.digest(tau.encoded(row))
                value["receipts"][role + "-tokenizer.json"] = tau.encoded(token).decode()
                endpoints.append(rpc.identity.Endpoint(row["endpoint"]["port"], {"process": row["process"]}))
            raw = session.probes.encoded(value)
            with fixtures.pipe() as (rd, wr), fixtures.pipe() as (out_rd, out_wr):
                failures, observed = [], []
                until = time.monotonic() + 4
                def control():
                    proxy = None
                    try:
                        with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                            observed.append(stream.readline(32769))
                        proxy = rpc.Coordinator(rpc.transport.Channel(os.dup(out_rd), os.dup(wr)), adapter.binding(raw), until)
                        for role in session.ROLES:
                            client = proxy.client(role)
                            self.assertEqual(client.count(request(role), 4096, until), 7)
                            self.assertEqual(client.complete(request(role), 7, until), rpc_fixtures.completion())
                        observed.append(proxy.finish("evaluated", until))
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        if proxy is not None:
                            proxy.close()
                thread = threading.Thread(target=control, daemon=True)
                thread.start()
                try:
                    with mock.patch.object(session.identity, "source_receipt"), \
                            mock.patch.object(rpc.identity.Endpoint, "check") as check, \
                            mock.patch.object(adapter.cpu, "MeasuredClient", Measured):
                        result = adapter.serve(raw, endpoints, processes, fixtures.PRODUCER, fixtures.IMAGE,
                                               until, read_fd=rd, write_fd=out_wr)
                    self.assertGreaterEqual(check.call_count, 14)
                finally:
                    thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertFalse(failures)
                self.assertEqual(result, {"status": "evaluated", "completed_requests": 2})
                self.assertEqual(observed, [session.READY + raw + b"\n", result])
                self.assertTrue(all(p.poll() is None for p in processes))
        finally:
            for process in processes:
                stop(process)

    def test_private_relay_requires_finish_and_preserves_final_json_bytes(self):
        raw = session.probes.encoded(fixtures.packet())
        bound = adapter.binding(raw)
        for case in ("good", "wrong-count", "wrong-binding", "eof", "expired"):
            with self.subTest(case=case), fixtures.pipe() as (rd, wr), fixtures.pipe() as (out_rd, out_wr):
                script = """
import json,sys
p=json.loads(sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(4),'big')))
r={k:p[k] for k in ('schema','binding_sha256','sequence')}
r.update(kind='finish-result',status=p['status'],completed_requests=0)
if CASE=='wrong-count': r['completed_requests']=1
if CASE=='wrong-binding': r['binding_sha256']='f'*64
if CASE=='eof': sys.exit(1)
raw=json.dumps(r,separators=(',',':'),sort_keys=True).encode()
sys.stdout.buffer.write(len(raw).to_bytes(4,'big')+raw+b'{"final":true}\\n')
sys.stdout.flush()
sys.stdin.buffer.read()
""".replace("CASE", repr(case))
                child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", script],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                caller = rpc.transport.Channel(os.dup(out_rd), os.dup(wr))
                until = time.monotonic() + 3
                packet = {"schema": rpc.SCHEMA, "binding_sha256": tau.digest(tau.encoded(bound)),
                          "sequence": 1, "kind": "finish", "status": "budget_abort",
                          "deadline": time.monotonic() - 1 if case == "expired" else until - 0.5}
                try:
                    caller.send(packet, until)
                    if case == "good":
                        result = adapter.relay(child, raw, until, read_fd=rd, write_fd=out_wr)
                        self.assertEqual(result, {"status": "budget_abort", "completed_requests": 0})
                        self.assertEqual(caller.receive(until)["completed_requests"], 0)
                        self.assertEqual(child.stdout.readline(64), b'{"final":true}\n')
                    else:
                        with self.assertRaises(rpc.worker.Abort):
                            adapter.relay(child, raw, until, read_fd=rd, write_fd=out_wr)
                finally:
                    caller.close()
                    child.stdin.close()
                    stop(child)
                    child.stdout.close()

    def test_modes_are_explicit_and_mutually_exclusive_before_launch(self):
        for kwargs in ({"selected_owner": 1}, {"selected_owner": True, "probe": {}}):
            with self.assertRaises(ValueError), mock.patch.object(session.subprocess, "Popen") as spawn:
                session.run(None, [], None, fixtures.PRODUCER, fixtures.IMAGE, **kwargs)
            spawn.assert_not_called()
        worker = mock.Mock()
        with mock.patch.dict(fixtures.rec.main.__globals__, worker=worker), \
                mock.patch.object(sys, "argv", ["record", "--selected-owner-worker", "image", "revision"]):
            fixtures.rec.main()
            worker.assert_called_once_with(["image", "revision"], selected_owner=True)

    def test_whole_supervisor_joins_original_ready_and_terminal_owner_record(self):
        raw = session.probes.encoded(fixtures.packet())
        with mock.patch.object(session.identity, "source_receipt"):
            _, held = session.accept(raw, fixtures.PRODUCER, fixtures.IMAGE, time.monotonic() + 5)
        terminal = {**fixtures.result(held), "schema": "nib.tau-cpu-session/v4",
                    "owner": {"status": "evaluated", "completed_requests": 1}}
        script = """
import json,sys
sys.stdout.buffer.write(READY);sys.stdout.flush()
for op in ('count','complete','finish'):
    p=json.loads(sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(4),'big')))
    assert p['kind']==op
    r={k:p[k] for k in ('schema','binding_sha256','sequence')}
    r['kind']=op+'-result'
    if op=='finish': r.update(status='evaluated',completed_requests=1)
    else:
        import hashlib
        r['request_sha256']=hashlib.sha256(json.dumps(p['body'],sort_keys=True,separators=(',',':')).encode()+b'\\n').hexdigest()
        if op=='count': r['prompt_tokens']=7
        else: r['response']=RESPONSE
    b=json.dumps(r,separators=(',',':'),sort_keys=True).encode()
    sys.stdout.buffer.write(len(b).to_bytes(4,'big')+b);sys.stdout.flush()
sys.stdin.buffer.read()
sys.stdout.buffer.write(FINAL);sys.stdout.flush()
""".replace("READY", repr(session.READY + raw + b"\n")).replace("RESPONSE", repr(rpc_fixtures.completion())).replace(
            "FINAL", repr(session.probes.encoded(terminal)))
        with tempfile.TemporaryDirectory() as tmp, fixtures.pipe() as (rd, wr), fixtures.pipe() as (out_rd, out_wr):
            fd, failures, observed = os.open(tmp, os.O_DIRECTORY), [], []
            until = time.monotonic() + 4
            def controller():
                proxy = None
                try:
                    with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                        observed.append(stream.readline(32769))
                    proxy = rpc.Coordinator(rpc.transport.Channel(os.dup(out_rd), os.dup(wr)), adapter.binding(raw), until)
                    client = proxy.client("simulator")
                    client.count(request("simulator"), 4096, until)
                    client.complete(request("simulator"), 7, until)
                    proxy.finish("evaluated", until)
                except BaseException as error:
                    failures.append(error)
                finally:
                    if proxy:
                        proxy.close()
            thread = threading.Thread(target=controller, daemon=True)
            thread.start()
            snapshot = mock.Mock()
            snapshot.cleanup_process_group.side_effect = stop
            try:
                with mock.patch.object(session.identity, "source_receipt"):
                    actual = session.relay([sys.executable, "-I", "-S", "-B", "-c", script], {}, snapshot,
                        fixtures.PRODUCER, fixtures.IMAGE, fd, read_fd=rd, write_fd=out_wr, selected_owner=True)
                self.assertEqual(actual, terminal)
            finally:
                os.close(fd)
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures)
            self.assertEqual(observed, [session.READY + raw + b"\n"])
            snapshot.cleanup_process_group.assert_called_once()


if __name__ == "__main__":
    unittest.main()
