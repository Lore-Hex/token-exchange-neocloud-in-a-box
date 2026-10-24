# SPDX-License-Identifier: Apache-2.0
"""GPU-free opt-in payload and actual private-pipe supervisor regressions."""

import copy
import os
import sys
import threading
import time
import unittest
from unittest import mock

import tau_gpu_owner as gpu
import tau_gpu_sources as sources
import test_tau_gpu_owner as fixtures
import test_tau_cpu_owner as cpu
import test_deepseek_tuning as tuning

host, lab, root = tuning.host, tuning.lab, tuning.ROOT


class GPULabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tuning.TuningProfiles.setUpClass()
        cls.args = list(tuning.TuningProfiles.args)
        names = (host.TAU_SOURCES, *("scripts/" + name + ".py" for name in sources.NAMES))
        cls.args[3] = {**cls.args[3], **{name: (root / name).read_bytes() for name in names}}
        cls.options = dict(model_profile="uncensored", execution_profile="tau8k-eager",
                           tokenizer_parity=True, tokenizer_materialization=True, runtime_observation=True)

    def test_disabled_owner_does_not_require_optional_sources_or_load_code(self):
        with mock.patch.object(host, "load") as load:
            for contract in ({"base_image": "fixed"}, {"sources": {}}):
                self.assertIsNone(host.owner_module(contract, "/unused"))
            with self.assertRaises(ValueError):
                host.owner_module({"sources": {"tau-owner-sources.py": "a" * 64}}, "/unused")
            load.assert_not_called()

    def test_opt_in_payload_is_source_bound_and_default_stays_unchanged(self):
        original, default = host.make_payload(*self.args, **self.options)
        files, contract = host.make_payload(*self.args, **self.options, tau_owner=True)
        self.assertNotIn("tau_owner", default)
        self.assertNotIn("tau-owner-sources.py", original)
        self.assertEqual(contract["tau_owner"], {"schema": gpu.SCHEMA})
        self.assertFalse(contract["confidential"])
        self.assertFalse(contract["deployable"])
        self.assertLessEqual(len(contract["sources"]), 110)
        for name in original.keys() - {"lab.json", "python"}:
            self.assertEqual(files[name], original[name])
        with tuning.fixtures.payload_tree(files) as path:
            owner = host.owner_module(contract, path)
            self.assertEqual(owner.selected(contract), gpu.enabled(contract))
            self.assertEqual(host.memory_gib(contract, path), 512)
            argv = host.create_command("fixed", "owner", path, "/weights", contract, {"config": {"Env": []}})
            self.assertIn("--interactive", argv)
            self.assertEqual(argv[argv.index("--network") + 1], "none")
            self.assertEqual(argv[argv.index("--memory-swap") + 1], "512g")
            self.assertNotIn("--publish", argv)
            plan = tuning.fixtures.PayloadProfiles.fixture.builder.recorder.prep.launch_plan()
            with mock.patch.object(lab, "read_source", side_effect=lambda p, *_: files[p.removeprefix("/opt/nib/")]):
                lab.validate_settings(contract, plan, 2)
            bad = copy.deepcopy(contract)
            bad["sources"][sources.PREFIX + "tau_owner_channel.py"] = "a" * 64
            with self.assertRaises(ValueError):
                host.owner_module(bad, path)
        for options in ({"tau_owner": 1}, {"tau_owner": True, "tokenizer_parity": False},
                        {"tau_owner": True, "execution_profile": "batch8-eager"},
                        {"tau_owner": True, "stream_cohort": True}):
            with self.subTest(options=options), self.assertRaises((ValueError, gpu.worker.Abort)):
                host.make_payload(*self.args, **{**self.options, **options})

    def test_cli_requires_explicit_closed_profile(self):
        argv = [host.SCRIPT, "--prepare-nonconfidential-uncensored-b300-dp2", "/unused",
                "--model-profile", "uncensored", "--execution-profile", "tau8k-eager", "--tau-owner",
                "--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
            self.assertTrue(prepare.call_args.kwargs["tau_owner"])
        for flag in ("--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation"):
            with mock.patch.object(sys, "argv", [arg for arg in argv if arg != flag]), \
                    mock.patch.object(host, "prepare") as prepare, self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_private_capture_preserves_success_and_failure_json(self):
        for failure in (None, "count", "complete"):
            with self.subTest(failure=failure):
                self.capture(failure)

    def test_join_rejects_changed_parity_observation_and_false_success(self):
        ready = fixtures.packet()
        original = gpu.observer.encoded(ready)
        finish = {"status": "evaluated", "completed_requests": 1}
        value = {"tau_owner": {"ready_sha256": gpu.tau.digest(original), **finish},
                 "functional_pass": True, "engine_stopped": True, "tokenizer_parity": ready["parity"],
                 "runtime_observation": {"samples": [ready["sample"]]}}
        gpu.validate_join(original, finish, value)
        changes = (lambda r: r.update(engine_stopped=False), lambda r: r.update(tokenizer_parity=None),
                   lambda r: r["runtime_observation"].update(samples=[]),
                   lambda r: r["tau_owner"].update(completed_requests=2),
                   lambda r: r["runtime_observation"]["samples"][0].update(argv_sha256="f" * 64))
        for change in changes:
            bad = copy.deepcopy(value)
            change(bad)
            with self.assertRaises(gpu.worker.Abort):
                gpu.validate_join(original, finish, bad)
        failure = {**value, "tau_owner": None, "functional_pass": False}
        gpu.validate_join(original, None, failure)
        with self.assertRaises(gpu.worker.Abort):
            gpu.validate_join(original, None, {**failure, "functional_pass": True})

    def test_capture_deadline_reaps_child_without_promoting_partial_output(self):
        contract = {**fixtures.contract(), "tau_owner": {"schema": gpu.SCHEMA}}
        snapshot = mock.Mock()
        snapshot.cleanup_process_group.side_effect = cpu.stop
        with cpu.fixtures.pipe() as (rd, _), cpu.fixtures.pipe() as (_, wr):
            with self.assertRaises(gpu.worker.Abort):
                gpu.capture([sys.executable, "-I", "-S", "-B", "-c", "import time;time.sleep(30)"], {},
                    time.monotonic() + .1, snapshot, contract, fixtures.SHA, read_fd=rd, write_fd=wr)
        snapshot.cleanup_process_group.assert_called_once()
        self.assertIsNotNone(snapshot.cleanup_process_group.call_args.args[0].poll())

    def capture(self, failure):
        contract = {**fixtures.contract(), "tau_owner": {"schema": gpu.SCHEMA}}
        raw = gpu.observer.encoded(fixtures.packet())
        terminal = {"tau_owner": None if failure else {"ready_sha256": gpu.tau.digest(raw),
                    "status": "evaluated", "completed_requests": 1}, "cleanup": "confirmed"}
        code = """
import hashlib,json,sys
sys.stdout.buffer.write(READY);sys.stdout.flush()
for op in ('count','complete','finish'):
    p=json.loads(sys.stdin.buffer.read(int.from_bytes(sys.stdin.buffer.read(4),'big')))
    assert p['kind']==op
    r={k:p[k] for k in ('schema','binding_sha256','sequence')}
    r['kind']=op+'-result'
    if op=='finish':r.update(status='evaluated',completed_requests=1)
    else:
        r['request_sha256']=hashlib.sha256(json.dumps(p['body'],sort_keys=True,separators=(',',':')).encode()+b'\\n').hexdigest()
        if op=='count':r['prompt_tokens']=7
        else:r['response']=RESPONSE
    if op==FAILED:
        r={k:r[k] for k in ('schema','binding_sha256','sequence','request_sha256')}
        r.update(kind=op+'-error',status='budget_abort')
    b=json.dumps(r,sort_keys=True,separators=(',',':')).encode()
    sys.stdout.buffer.write(len(b).to_bytes(4,'big')+b);sys.stdout.flush()
    if op==FAILED:break
sys.stdin.buffer.read()
sys.stdout.buffer.write(FINAL);sys.stdout.flush()
""".replace("READY", repr(gpu.READY + raw + b"\n")).replace("RESPONSE", repr(fixtures.fixtures.completion())).replace(
            "FAILED", repr(failure)).replace("FINAL", repr(gpu.tau.encoded(terminal)))
        with cpu.fixtures.pipe() as (rd, wr), cpu.fixtures.pipe() as (out_rd, out_wr):
            errors, seen = [], []
            deadline = time.monotonic() + 4
            def control():
                proxy = None
                try:
                    with os.fdopen(os.dup(out_rd), "rb", buffering=0) as stream:
                        seen.append(stream.readline(32769))
                    proxy = gpu.owners.Coordinator(gpu.owners.transport.Channel(os.dup(out_rd), os.dup(wr)),
                        gpu.binding(raw, contract, fixtures.SHA), deadline)
                    client = proxy.client("candidate")
                    body = fixtures.fixtures.body("candidate")
                    body["model"] = gpu.enabled(contract)
                    try:
                        client.count(body, 8192, deadline)
                        client.complete(body, 7, deadline)
                        proxy.finish("evaluated", deadline)
                        self.assertIsNone(failure)
                    except gpu.worker.Abort as error:
                        self.assertIsNotNone(failure)
                        self.assertEqual(error.status, "budget_abort")
                except BaseException as error:
                    errors.append(error)
                finally:
                    if proxy:
                        proxy.close()
            thread = threading.Thread(target=control, daemon=True)
            snapshot = mock.Mock()
            snapshot.cleanup_process_group.side_effect = cpu.stop
            thread.start()
            try:
                result = gpu.capture([sys.executable, "-I", "-S", "-B", "-c", code], {}, deadline,
                    snapshot, contract, fixtures.SHA, read_fd=rd, write_fd=out_wr)
                self.assertEqual(result[:3], (0, gpu.tau.encoded(terminal), raw))
                self.assertEqual(result[3], None if failure else {"status": "evaluated", "completed_requests": 1})
            finally:
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors)
            self.assertEqual(seen, [gpu.READY + raw + b"\n"])
            self.assertTrue(os.get_blocking(rd))
            self.assertTrue(os.get_blocking(out_wr))
            snapshot.cleanup_process_group.assert_called_once()


if __name__ == "__main__":
    unittest.main()
