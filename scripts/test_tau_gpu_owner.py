# SPDX-License-Identifier: Apache-2.0
"""Synthetic kernel facts and private pipes, never GPU hardware evidence."""

import copy
import os
import subprocess
import sys
import threading
import time
import unittest
import hashlib
import types
from pathlib import Path
from unittest import mock

import tau_gpu_owner as gpu
import test_engine_runtime_observation as runtime
import test_engine_tokenizer_parity as tokenizer
import test_tau_cpu_session as cpu
import test_tau_owner_channel as fixtures

rpc, tau = gpu.owners, gpu.tau
SHA = "d" * 64


def contract():
    return {**runtime.contract(), "model_profile": gpu.policy.models.select("uncensored"),
            "gpu_security": "nonconfidential", "data_parallel_size": 2, "execution_profile": "tau8k-eager",
            "tokenizer_parity": True, "tokenizer_materialization": {"fixture": True}}


def measured():
    def request(path, body, deadline):
        return tokenizer.response(path, body, deadline, max_model_len=8192)
    return tokenizer.parity.run(request, tokenizer.local(), tokenizer.lab.MODEL, tokenizer.correctness,
                                deadline=time.monotonic() + 5, max_model_len=8192)


def packet(pid=123):
    observed = {"pid": pid, "parent": os.getpid(), "start": 42,
                "namespaces": {"net": 101, "pid": 102, "cgroup": 103}, "boot": "b" * 64, "argv": "c" * 64}
    process = {"pid": pid, "start_ticks": 42, "boot_id_sha256": "b" * 64,
               "netns_inode": 101, "exe_sha256": contract()["interpreter"]["sha256"],
               "argv_sha256": "c" * 64, "listener_inode": 111}
    sample = {**runtime.sample(), "process_sha256": gpu.observer.digest(gpu.observer.encoded(observed)),
              "argv_sha256": observed["argv"],
              "listener_sha256": gpu.observer.digest(gpu.observer.encoded({"process": observed, "inode": 111}))}
    return {"schema": "nib.tau-gpu-owner-ready/v1", "contract_sha256": SHA,
            "model": contract()["model_profile"]["served_model_name"], "expires_unix": int(time.time()) + 60,
            "runtime": observed, "process": process, "sample": sample, "parity": measured()}


class Client:
    def __init__(self, endpoint, deployment):
        self.endpoint, self.deployment = endpoint, deployment

    def count(self, body, context, deadline):
        self.deployment.fresh()
        return 7

    def complete(self, body, count, deadline):
        self.deployment.fresh()
        return fixtures.completion()


class GPUOwnerTests(unittest.TestCase):
    def test_source_closure_isolated_and_rejects_tampering(self):
        import tau_gpu_sources as closure
        root = Path(__file__).resolve().parent
        sources = {closure.PREFIX + name + ".py": (root / (name + ".py")).read_bytes() for name in closure.NAMES}
        pins = {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}
        poison = {name: types.ModuleType(name) for name in closure.NAMES}
        with mock.patch.dict(sys.modules, poison):
            loaded = closure.load(sources, pins)
            self.assertIs(loaded.owners.worker, loaded.owners.identity.local.worker)
            self.assertIs(loaded.tau, loaded.policy.tau)
            self.assertIs(loaded.owners.cpu.probes, loaded.parity)
            self.assertIsNot(loaded, poison["tau_gpu_owner"])
        for name in sources:
            with self.subTest(name=name), self.assertRaises(ValueError):
                closure.load({**sources, name: sources[name] + b"\n"}, pins)

    def test_ready_binds_selected_model_8k_parity_and_original_process(self):
        value = packet()
        raw = gpu.observer.encoded(value)
        bound = gpu.binding(raw, contract(), SHA)
        self.assertEqual(bound, {"ready_sha256": tau.digest(raw), "roles": {"candidate": {
            "model": value["model"], "context_tokens": 8192}}})
        self.assertFalse(value["parity"]["tau_acceptance"])
        changes = (lambda r: r.update(schema="unknown"), lambda r: r.update(contract_sha256="a" * 64),
                   lambda r: r.update(model="original"), lambda r: r.update(expires_unix=int(time.time()) - 1),
                   lambda r: r.update(expires_unix=int(time.time()) + 1000), lambda r: r.update(expires_unix=True),
                   lambda r: r["process"].update(pid=124), lambda r: r["process"].update(netns_inode=True),
                   lambda r: r["runtime"].update(parent=1), lambda r: r["sample"].update(event="after-requests"),
                   lambda r: r["sample"].update(listener_sha256="f" * 64),
                   lambda r: r["parity"].update(max_model_len=4096), lambda r: r["parity"].update(all_cases_pass=False),
                   lambda r: r["parity"]["cases"][0]["server"].update(count=9),
                   lambda r: r.update(unexpected="private"))
        for change in changes:
            altered = copy.deepcopy(value)
            change(altered)
            with self.subTest(change=change), self.assertRaises((ValueError, rpc.worker.Abort)):
                gpu.binding(gpu.observer.encoded(altered), contract(), SHA)
        with self.assertRaises(rpc.worker.Abort):
            gpu.binding(raw + b" ", contract(), SHA)

    def test_only_closed_selected_ordinary_8k_profile_can_attach(self):
        for key, value in (("model_profile", gpu.policy.models.select("original")),
                           ("execution_profile", "batch8-graphs"), ("gpu_security", "confidential"),
                           ("gpu_family", "B200"), ("data_parallel_size", True), ("data_parallel_size", 1),
                           ("tokenizer_parity", False), ("runtime_observation", False),
                           ("stream_cohort", {}), ("provider_conformance", {})):
            with self.subTest(key=key), self.assertRaises(rpc.worker.Abort):
                gpu.enabled({**contract(), key: value})

    def test_process_exit_or_expiry_never_passes_freshness(self):
        child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "pass"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child.wait(timeout=3)
        with self.assertRaises(rpc.worker.Abort):
            gpu.Lifetime(child, time.monotonic() + 10)
        life = object.__new__(gpu.Lifetime)
        life.process, life.deadline = child, time.monotonic() - 1
        with self.assertRaises(rpc.worker.Abort) as caught:
            life.fresh()
        self.assertEqual(caught.exception.status, "timeout")

    def test_actual_private_pipe_owns_candidate_and_requires_finish(self):
        child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(10)"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        value, failures, observed = packet(child.pid), [], []
        try:
            with cpu.pipe() as (rd, wr), cpu.pipe() as (out_rd, out_wr):
                until = time.monotonic() + 5
                def controller():
                    proxy = None
                    try:
                        with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                            line = stream.readline(16385)
                        self.assertTrue(line.startswith(gpu.READY))
                        raw = line[len(gpu.READY):-1]
                        observed.append(raw)
                        proxy = rpc.Coordinator(rpc.transport.Channel(os.dup(out_rd), os.dup(wr)),
                                                gpu.binding(raw, contract(), SHA), until - 1.5)
                        client = proxy.client("candidate")
                        body = fixtures.body()
                        body["model"] = value["model"]
                        client.count(body, 8192, until - 1.5)
                        client.complete(body, 7, until - 1.5)
                        proxy.finish("evaluated", until - 1.5)
                    except BaseException as error:
                        failures.append(error)
                    finally:
                        if proxy is not None:
                            proxy.close()
                thread = threading.Thread(target=controller, daemon=True)
                thread.start()
                try:
                    facts = {key: value["process"][key] for key in ("start_ticks", "boot_id_sha256", "netns_inode", "argv_sha256")}
                    with mock.patch.object(gpu.observer, "process", return_value=value["runtime"]), \
                            mock.patch.object(gpu.observer, "listener", return_value=111), \
                            mock.patch.object(gpu.identity, "process_facts", return_value=facts), \
                            mock.patch.object(gpu.identity.Endpoint, "check") as check, \
                            mock.patch.object(gpu.identity, "GuardedClient", Client):
                        result = gpu.serve(child, contract(), SHA, value["sample"], value["parity"], until,
                                           read_fd=rd, write_fd=out_wr)
                    self.assertGreaterEqual(check.call_count, 5)
                finally:
                    thread.join(6)
                self.assertFalse(thread.is_alive())
                self.assertFalse(failures)
                self.assertEqual(result, {"ready_sha256": tau.digest(observed[0]), "status": "evaluated", "completed_requests": 1})
                self.assertTrue(os.get_blocking(rd) and os.get_blocking(out_wr))
                self.assertIsNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
