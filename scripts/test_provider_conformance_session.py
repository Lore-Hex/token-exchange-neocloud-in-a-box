# SPDX-License-Identifier: Apache-2.0
"""Owned-session software controls. Synthetic fixtures are not engine evidence."""

import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import provider_conformance_session as session
import provider_conformance_worker as worker
import engine_local_correctness as correctness
import engine_runtime_observation as observer
import test_deepseek_lab as fixtures
import test_engine_runtime_observation as observations

host, lab = fixtures.host, fixtures.lab
ROOT = fixtures.ROOT
SHA = session.digest(b"synthetic-provider-contract")
MANIFEST = "sha256:" + session.digest(b"synthetic-image-manifest")
OWNER, ENGINE = "a" * 32, "b" * 64
NAME = "nib-deepseek-" + OWNER


def contract():
    return {**observations.contract(), "data_parallel_size": 2, "tokenizer_parity": True,
            "tokenizer_materialization": {"synthetic": True},
            "provider_conformance": {"schema": "nib.owned-provider-input/v1", "image": session.IMAGE,
                                     "payload_sha256": SHA}}


def sample(event="before-requests"):
    value = observations.sample(event)
    value["cgroup"]["memory.max"] = 1024 << 30
    return value


def packet():
    return {"schema": "nib.owned-provider-ready/v1", "contract_sha256": SHA,
            "parity_pass": True, "sample": sample()}


def result(accepted=True):
    checks = {name: "pass" for name in worker.MANDATORY}
    checks["catalog.declared-v2"] = "skip"
    if not accepted:
        checks["chat.pong"] = "warn"
    return worker.result("completed", {"checks": checks, "upstream_conformance_gate": True,
        "mandatory_checks_passed": accepted, "native_identity_matches": True})


def run_row(accepted=True):
    return {"schema": "nib.owned-provider-run/v1", "stage": "completed", "accepted": accepted,
            "cleanup": "confirmed", "worker": result(accepted), "worker_exit_code": 0 if accepted else 1,
            "namespace_verified": True, "engine_identity_sha256": SHA, "image_manifest": MANIFEST}


def image_info():
    return {"Architecture": "amd64", "Os": "linux", "Config": {}, "Id": MANIFEST,
            "RepoDigests": [session.IMAGE], "Descriptor": {"digest": MANIFEST,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "platform": {"architecture": "amd64", "os": "linux"}}}


def container_info(command, payload):
    return {"Id": "c" * 64, "Name": "/" + NAME + "-provider", "Image": session.IMAGE.split("@")[1],
        "ImageManifestDescriptor": {"digest": MANIFEST},
        "State": {"Status": "running", "Running": True, "Pid": 456, "OOMKilled": False,
                  "StartedAt": "synthetic-start", "ExitCode": 0},
        "Config": {"Image": session.IMAGE, "Labels": {session.LABEL: OWNER}, "Entrypoint": ["/usr/bin/env"],
                   "Cmd": command[command.index(session.IMAGE) + 1:], "User": "65534:65534", "WorkingDir": "/scratch",
                   "Tty": False, "OpenStdin": False, "Healthcheck": {"Test": ["NONE"]}},
        "HostConfig": {"NetworkMode": "container:" + ENGINE, "ReadonlyRootfs": True, "Runtime": "runc",
            "CapDrop": ["ALL"], "Privileged": False, "IpcMode": "private", "CgroupnsMode": "private",
            "Memory": 1 << 30, "MemorySwap": 1 << 30, "NanoCpus": 1000000000, "PidsLimit": 32,
            "ShmSize": 16 << 20, "RestartPolicy": {"Name": "no"}, "LogConfig": {"Type": "none"},
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/scratch": "rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=65534,gid=65534"}},
        "Mounts": [{"Type": "bind", "Source": str(payload), "Destination": "/opt/nib", "RW": False}]}


class SchemaTests(unittest.TestCase):
    def test_explicit_profile_and_no_optional_relaxations(self):
        session.enabled(contract())
        for change in ({"data_parallel_size": 1}, {"data_parallel_size": True}, {"gpu_family": "B200"},
                       {"gpu_security": "nonconfidential"}, {"tokenizer_parity": False},
                       {"runtime_observation": False}, {"operation": "pre-serve"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                session.enabled({**contract(), **change})
        for key in ("tokenizer_materialization", "provider_conformance"):
            changed = contract()
            del changed[key]
            with self.assertRaises(ValueError):
                session.enabled(changed)
        changed = contract()
        changed["provider_conformance"]["endpoint"] = "arbitrary"
        with self.assertRaises(ValueError):
            session.enabled(changed)

    def test_ready_strict_private_projection_and_join(self):
        ready = packet()
        self.assertEqual(session.validate_ready(session.encoded(ready), contract(), SHA, observer), ready)
        for change in (lambda r: r.update(contract_sha256="0" * 64), lambda r: r.update(parity_pass=False),
                       lambda r: r.update(body="PRIVATE"), lambda r: r["sample"].update(event="after-requests"),
                       lambda r: r["sample"].update(stdio_null=False)):
            changed = copy.deepcopy(ready)
            change(changed)
            with self.assertRaises(ValueError):
                session.validate_ready(session.encoded(changed), contract(), SHA, observer)
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b"PRIVATE", b" " * 4097):
            with self.assertRaises(ValueError):
                session.validate_ready(raw, contract(), SHA, observer)
        for accepted in (True, False):
            row = run_row(accepted)
            ack = {"ready_sha256": session.digest(session.encoded(ready)),
                   "report_sha256": session.digest(session.encoded(row)), "accepted": accepted}
            experiment = {"provider_conformance": ack, "functional_pass": accepted,
                "tokenizer_parity": {"all_cases_pass": True}, "runtime_observation": {
                    "contract_sha256": SHA, "samples": [sample(), sample("after-requests")]}}
            session.validate_join(row, ready, experiment, worker)
            for change in (lambda e: e["provider_conformance"].update(report_sha256="0" * 64),
                           lambda e: e["provider_conformance"].update(ready_sha256="0" * 64),
                           lambda e: e["runtime_observation"]["samples"][0].update(exe_sha256="0" * 64),
                           lambda e: e["tokenizer_parity"].update(all_cases_pass=False)):
                changed = copy.deepcopy(experiment)
                change(changed)
                with self.assertRaises(ValueError):
                    session.validate_join(row, ready, changed, worker)

    def test_worker_verdict_status_identity_and_exit_cannot_be_upgraded(self):
        self.assertTrue(session.validate_worker(result(), worker, 0))
        self.assertFalse(session.validate_worker(result(False), worker, 1))
        self.assertFalse(session.validate_worker(worker.result("rejected"), worker, 2))
        changes = [lambda r: r.update(endpoint_identity_verified=True), lambda r: r.update(body="PRIVATE"),
                   lambda r: r.update(checker_version="other"),
                   lambda r: r["projection"].update(native_identity_matches=False),
                   lambda r: r["projection"]["checks"].update({"chat.pong": "warn"}),
                   lambda r: r["projection"]["checks"].update({"unknown": "pass"}),
                   lambda r: r["projection"]["checks"].pop("stream.done")]
        for change in changes:
            changed = result()
            change(changed)
            with self.assertRaises(ValueError):
                session.validate_worker(changed, worker, 0)
        with self.assertRaises(ValueError):
            session.validate_worker(result(False), worker, 0)


class ProcessTests(unittest.TestCase):
    def invoke(self, code, *, ready=None, seconds=5):
        return session.capture([sys.executable, "-I", "-S", "-B", "-c", code],
            {"PATH": "/usr/bin:/bin", "LANG": "C"}, time.monotonic() + seconds, fixtures.snapshot, ready=ready)

    def test_real_pipe_handshake_and_guest_bound_ack(self):
        path = str(ROOT / "scripts/provider_conformance_session.py")
        code = ("import types,json,time; m=types.ModuleType('session'); "
                "exec(compile(open(" + repr(path) + ",'rb').read(),'session','exec'),m.__dict__); "
                "a=m.guest_wait(" + repr(SHA) + ",{},time.monotonic()+4); print(json.dumps(a))")
        def ready(raw):
            self.assertEqual(session.decode(raw)["contract_sha256"], SHA)
            return {"ready_sha256": session.digest(raw), "report_sha256": SHA, "accepted": False}
        code_out, raw = self.invoke(code, ready=ready)
        self.assertEqual(code_out, 0)
        self.assertFalse(session.decode(raw)["accepted"])
        with self.assertRaises(ValueError):
            self.invoke(code, ready=lambda raw: {**ready(raw), "ready_sha256": "0" * 64})

    def test_caps_stderr_timeout_and_callback_failure_reap_processes(self):
        for code in ("import os; os.write(1,b'x'*65537)", "import os; os.write(2,b'PRIVATE')",
                     "import time; time.sleep(30)"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.invoke(code, seconds=0.2)
        emitted = "import os,time; os.write(1," + repr(session.READY + b"{}\n") + "); time.sleep(30)"
        with self.assertRaises(ValueError):
            self.invoke(emitted, ready=lambda _: session.require(False))
        for raw in (session.READY + b"{}\nTRAILING", session.READY + b" " * 4097 + b"\n"):
            with self.assertRaises(ValueError):
                self.invoke("import os; os.write(1," + repr(raw) + ")", ready=lambda _: {})
        self.assertEqual(self.invoke("print('{}')", ready=lambda _: self.fail("not a readiness frame")), (0, b"{}\n"))

    def test_guest_eof_and_oversized_ack_fail_closed(self):
        for raw in (b"", b"x" * 513, session.DONE + b"{}\n", session.DONE + b"{}\n{}\n"):
            reader, writer = os.pipe()
            output = os.open(os.devnull, os.O_WRONLY)
            try:
                os.write(writer, raw)
                os.close(writer)
                with self.assertRaises(ValueError):
                    session.guest_wait(SHA, {}, time.monotonic() + 1, read_fd=reader, write_fd=output)
            finally:
                os.close(reader)
                os.close(output)


class ContainerTests(unittest.TestCase):
    def test_tagless_digest_reference_preserves_exact_pin_and_platform(self):
        repository, pinned_digest = session.IMAGE.split("@")
        canonical = repository.rsplit(":", 1)[0] + "@" + pinned_digest
        for reference in (session.IMAGE, canonical):
            for prefix in ("", "docker.io/", "docker.io/library/"):
                row = image_info()
                row["RepoDigests"] = [reference.removeprefix(prefix)]
                with self.subTest(reference=row["RepoDigests"]):
                    self.assertEqual(session.image_manifest(row), MANIFEST)
        for references in ([], canonical, [None], [canonical + "extra"],
                           [canonical.replace(pinned_digest, "sha256:" + "0" * 64)],
                           [canonical.replace("/python@", "/other@")],
                           ["untrusted.invalid/" + canonical], [repository],
                           [canonical.replace("@", ":other@")]):
            row = image_info()
            row["RepoDigests"] = references
            with self.subTest(references=references), self.assertRaises(ValueError):
                session.image_manifest(row)
        for change in (lambda r: r.update(Architecture="arm64"), lambda r: r.update(Os="windows"),
                       lambda r: r["Config"].update(Volumes={"/unsafe": {}}),
                       lambda r: r.update(Id="sha256:" + "0" * 64),
                       lambda r: r["Descriptor"].update(digest="sha256:" + "0" * 64),
                       lambda r: r["Descriptor"].update(mediaType="application/vnd.oci.image.index.v1+json"),
                       lambda r: r["Descriptor"].update(platform={"architecture": "arm64", "os": "linux"})):
            row = image_info()
            row["RepoDigests"] = [canonical.removeprefix("docker.io/library/")]
            change(row)
            with self.assertRaises(ValueError):
                session.image_manifest(row)

    def test_exact_cpu_create_and_inspect_reject_confinement_drift(self):
        payload = Path("/private/checker/payload")
        command = session.create_command(NAME + "-provider", OWNER, ENGINE, payload, SHA, worker)
        self.assertEqual(command[command.index("--network") + 1], "container:" + ENGINE)
        for flag in ("--gpus", "--publish", "--hostname", "--dns", "--pid", "--privileged"):
            self.assertNotIn(flag, command)
        self.assertEqual(session.image_manifest(image_info()), MANIFEST)
        good = container_info(command, payload)
        session.check_container(good, command, NAME + "-provider", OWNER, ENGINE, payload, MANIFEST)
        for key, value in (("NetworkMode", "host"), ("ReadonlyRootfs", False), ("Runtime", "nvidia"),
                           ("CapAdd", ["SYS_ADMIN"]), ("DeviceRequests", [{"Count": -1}]),
                           ("PidMode", "host"), ("CgroupnsMode", "host"), ("MemorySwap", -1),
                           ("PortBindings", {"8000": [{}]}), ("LogConfig", {"Type": "json-file"})):
            changed = copy.deepcopy(good)
            changed["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                session.check_container(changed, command, NAME + "-provider", OWNER, ENGINE, payload, MANIFEST)
        for change in (lambda r: r["Mounts"][0].update(RW=True), lambda r: r.update(Image="other"),
                       lambda r: r["Config"].update(Cmd=["unreviewed"]),
                       lambda r: r["Config"]["Labels"].update({session.LABEL: "foreign"})):
            changed = copy.deepcopy(good)
            change(changed)
            with self.assertRaises(ValueError):
                session.check_container(changed, command, NAME + "-provider", OWNER, ENGINE, payload, MANIFEST)

    def test_cleanup_checker_precedes_engine_and_foreign_owner_never_removed(self):
        for foreign in (False, True):
            removals = []
            present = {NAME, NAME + "-provider"}
            def call(argv, *_args, **_kwargs):
                if "ls" in argv:
                    target = argv[-1].removeprefix("name=^/").removesuffix("$")
                    return b"exists" if target in present else b""
                self.assertIn("rm", argv)
                removals.append(argv[-1])
                present.remove(argv[-1])
                return b"removed"
            def info(_recorder, _kind, name, _env):
                label = session.LABEL if name.endswith("-provider") else host.LABEL
                return {"Config": {"Labels": {label: "foreign" if foreign and label == session.LABEL else OWNER}}}
            recorder = types.SimpleNamespace(call=call)
            with mock.patch.object(host, "inspect", side_effect=info):
                if foreign:
                    with self.assertRaises(ValueError):
                        host.cleanup(recorder, NAME, OWNER, {}, provider=True)
                    self.assertEqual(removals, [NAME])
                else:
                    host.cleanup(recorder, NAME, OWNER, {}, provider=True)
                    self.assertEqual(removals, [NAME + "-provider", NAME])

    def test_checker_lifecycle_failure_preservation_and_namespace_binding(self):
        payload = Path("/private/checker/payload")
        command = session.create_command(NAME + "-provider", OWNER, ENGINE, payload, SHA, worker)
        for mode in ("pass", "warn", "exit", "namespace", "owner-drift", "cleanup", "create", "oom", "watchdog"):
            cpu = container_info(command, payload)
            engine = {"Id": ENGINE, "State": {**cpu["State"], "Pid": 123}}
            identities = {123: {"net": 1, "pid": 2, "cgroup": 3},
                          456: {"net": 9 if mode == "namespace" else 1, "pid": 4, "cgroup": 5}}
            def stat(path):
                pieces = path.split("/")
                return types.SimpleNamespace(st_ino=identities[int(pieces[2])][pieces[-1]])
            calls = []
            def call(argv, *_args, **_kwargs):
                calls.append(argv)
                if argv[1:3] == ["image", "inspect"]:
                    return session.encoded([image_info()])
                if "ls" in argv: return b""
                if "create" in argv:
                    if mode == "create": raise TimeoutError("PRIVATE")
                    return b"c" * 64
                if "inspect" in argv: return session.encoded([cpu])
                self.fail("unexpected command")
            def capture(*args, after_start, **kwargs):
                after_start()
                if mode == "exit": raise ValueError("PRIVATE")
                accepted = mode != "warn"
                cpu["State"].update(Status="exited", Running=False, ExitCode=0 if accepted else 1,
                                    OOMKilled=mode == "oom")
                if mode == "owner-drift": engine["State"]["StartedAt"] = "changed"
                return (0 if accepted else 1), session.encoded(result(accepted))
            recorder = types.SimpleNamespace(call=call, snapshot=mock.Mock())
            cleaned = mock.Mock(side_effect=ValueError("PRIVATE") if mode == "cleanup" else None)
            monitor = mock.Mock(returncode=1 if mode == "watchdog" else 0)
            reader, writer = os.pipe()
            monitor_start = mock.Mock(return_value=(monitor, writer))
            try:
                with mock.patch.object(worker, "verify"), mock.patch.object(session.os, "stat", side_effect=stat), \
                        mock.patch.object(session, "capture", side_effect=capture):
                    row = session.run_checker(payload, contract(), worker, recorder, {}, NAME, OWNER, ENGINE,
                        lambda: engine, cleaned, monitor_start, time.monotonic() + 300)
                self.assertEqual(row["accepted"], mode == "pass", mode)
                self.assertNotIn("PRIVATE", json.dumps(row))
                cleaned.assert_called_once()
                if monitor_start.called:
                    self.assertLessEqual(monitor_start.call_args.args[0] - time.time(), 200)
                    monitor.wait.assert_called_once_with(timeout=45)
                else:
                    os.close(writer)
                if mode == "create": self.assertEqual(row["cleanup"], "create-completion-unknown")
            finally:
                os.close(reader)


class WiringTests(unittest.TestCase):
    def test_full_owner_handshake_join_and_cleanup_not_just_worker_gate(self):
        for mode in ("pass", "failed-worker", "bad-ack", "cleanup", "no-ready"):
            selected = {**contract(), "base_image": "synthetic-engine", "sources": {
                "provider-session.py": SHA, "provider-worker.py": SHA, "runtime-observation.py": SHA}}
            row = run_row(mode not in ("failed-worker", "no-ready"))
            ready = packet()
            ready["contract_sha256"] = session.digest(b"synthetic-contract")
            report = {"functional_pass": mode not in ("failed-worker", "no-ready"), "provider_conformance": None,
                "runtime_observation": {"contract_sha256": ready["contract_sha256"],
                    "samples": [sample(), sample("after-requests")]}, "tokenizer_parity": {"all_cases_pass": True}}
            info = {"Id": ENGINE, "Config": {"Labels": {host.LABEL: OWNER}},
                    "State": {"Status": "created", "ExitCode": 0, "OOMKilled": False}}
            def inspect(_recorder, kind, *_):
                return {"Id": "synthetic-image"} if kind == "image" else info
            def capture(*args, ready, **kwargs):
                self.assertIn("--interactive", args[0])
                if mode != "no-ready":
                    report["provider_conformance"] = ready(session.encoded(packet_value))
                if mode == "bad-ack": report["provider_conformance"]["report_sha256"] = "0" * 64
                info["State"]["Status"] = "exited"
                return 0, session.encoded(report)
            packet_value = ready
            def checker(*args):
                args[8]()  # Ownership check from the actual parent closure.
                args[9]()  # Checker cleanup callback must never delete the engine.
                return row
            modules = {"lab_provider_live": session, "lab_provider_live_worker": worker,
                       "lab_provider_live_observer": observer}
            recorder = types.SimpleNamespace(image_binding=lambda *_: {}, snapshot=fixtures.snapshot,
                call=mock.Mock(side_effect=[b"", ENGINE.encode()]))
            monitor = mock.Mock(returncode=0)
            monitor.poll.return_value = None
            def clean(*args, **kwargs):
                if kwargs == {"provider": True} and mode == "cleanup": raise ValueError("PRIVATE")
            with mock.patch.object(host, "load", side_effect=lambda name, *_: modules[name]), \
                    mock.patch.object(host, "read", return_value=b"synthetic-contract"), \
                    mock.patch.object(session, "preflight_image"), mock.patch.object(session, "capture", side_effect=capture), \
                    mock.patch.object(session, "run_checker", side_effect=checker) as run, \
                    mock.patch.object(host, "inspect", side_effect=inspect), mock.patch.object(host, "check_container"), \
                    mock.patch.object(host, "create_command", return_value=["docker", "container", "create"]), \
                    mock.patch.object(host, "validate_result"), mock.patch.object(host, "cleanup", side_effect=clean) as cleanup, \
                    mock.patch.object(host.subprocess, "Popen", return_value=monitor), \
                    mock.patch.object(host.uuid, "uuid4", return_value=types.SimpleNamespace(hex=OWNER)), \
                    mock.patch.object(host, "CANCELLED", False), mock.patch.object(host, "CLEANING", False):
                out = host.run_container(Path("/private/bundle"), SHA, Path("/private/weights"), time.time() + 2690,
                    recorder, selected, {}, host.controlled_env("unix:///var/run/docker.sock"))
            self.assertEqual(out["functional_pass"], mode == "pass", mode)
            self.assertEqual(run.call_count, 0 if mode == "no-ready" else 1)
            self.assertEqual(cleanup.call_args.kwargs, {"provider": True})
            if run.called: self.assertEqual(cleanup.call_args_list[0].kwargs, {"checker_only": True})
            self.assertFalse(out["confidential"])
            self.assertEqual(out["result"], "fail")
            self.assertNotIn("PRIVATE", json.dumps(out))

    def test_watchdog_eof_and_deadline_use_only_owned_checker(self):
        for triggered_by_eof in (True, False):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                marker = root / "cleaned"
                script = root / "watch.py"
                script.write_text(
                    "import sys,types,pathlib,time\nm=types.ModuleType('fixture');m.__file__=" + repr(host.__file__) + "\n"
                    "exec(compile(pathlib.Path(m.__file__).read_bytes(),m.__file__,'exec'),m.__dict__)\n"
                    "m.WATCHDOG_SETTLE_SECONDS=0\nm.bundle_inputs=lambda *a:(None,{'provider_conformance':{}},None,None)\n"
                    "def clean(*a,**kw):\n assert kw=={'provider':True,'checker_only':True}\n pathlib.Path(" +
                    repr(str(marker)) + ").write_text('confirmed')\nm.cleanup=clean\n"
                    "m.watchdog(None,None,'nib-deepseek-'+'a'*32,'b'*32,time.time()+" +
                    ("30" if triggered_by_eof else "0.1") +
                    ",int(sys.argv[1]),'unix:///var/run/docker.sock',checker_only=True)\n")
                reader, writer = os.pipe()
                process = subprocess.Popen([sys.executable, "-I", "-S", str(script), str(reader)], pass_fds=(reader,),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                os.close(reader)
                if triggered_by_eof: os.close(writer)
                try:
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual((process.returncode, stdout, stderr), (0, b"", b""))
                    self.assertEqual(marker.read_text(), "confirmed")
                finally:
                    if not triggered_by_eof: os.close(writer)
                    fixtures.snapshot.cleanup_process_group(process)

    def test_cli_requires_exact_explicit_preparation_profile(self):
        flags = ["--tokenizer-parity", "--tokenizer-materialization", "--runtime-observation",
                 "--provider-conformance-wheels", "/private/wheels"]
        good = [host.SCRIPT, "--prepare-local-correctness-b300-dp2", "/private/new", *flags]
        with mock.patch.object(host.sys, "argv", good), mock.patch.object(host, "prepare") as prepare:
            self.assertEqual(host.main(), 0)
        self.assertEqual(prepare.call_args.kwargs, {"tokenizer_parity": True, "tokenizer_materialization": True,
            "runtime_observation": True, "provider_wheelhouse": "/private/wheels"})
        bad = [[host.SCRIPT, option, "/private/new", *flags] for option in ("--prepare", "--run",
               "--prepare-local-correctness-b300", "--prepare-nonconfidential-baseline-b300")]
        bad += [[arg for arg in good if arg != removed] for removed in flags[:3]]
        for args in bad:
            with mock.patch.object(host.sys, "argv", args), mock.patch.object(host, "prepare") as prepare, \
                    self.assertRaises(ValueError):
                host.main()
            prepare.assert_not_called()

    def test_payload_default_unchanged_sources_pinned_and_only_stdin_changes(self):
        fixtures.PayloadTests.setUpClass()
        base = fixtures.PayloadTests
        extra = dict(base.extra)
        for name in (host.PARITY, host.OBSERVATION, host.PROVIDER, host.PROVIDER_WORKER):
            extra[name] = (ROOT / name).read_bytes()
        args = (base.builder, "1" * 40, base.bundle, extra, b"synthetic", "local-correctness", "B300", "confidential", 2)
        options = {"tokenizer_parity": True, "tokenizer_materialization": True, "runtime_observation": True}
        files, baseline = host.make_payload(*args, **options)
        self.assertEqual((files, baseline), host.make_payload(*args, **options, provider_conformance=None))
        enabled, selected = host.make_payload(*args, **options, provider_conformance=contract()["provider_conformance"])
        for name in ("arguments", "environment", "interpreter", "candidate_bundle_sha256"):
            self.assertEqual(selected[name], baseline[name])
        self.assertEqual(lab.STARTUP_SECONDS, 1800)
        for name, source in (("provider-session.py", host.PROVIDER), ("provider-worker.py", host.PROVIDER_WORKER)):
            self.assertNotIn(name, files)
            self.assertEqual(enabled[name], extra[source])
            self.assertEqual(selected["sources"][name], session.digest(extra[source]))
        with mock.patch.object(host, "read", return_value=b"synthetic-fixed-contract"):
            default = host.create_command(NAME, OWNER, "/private/bundle", "/private/weights", baseline, {"config": {"Env": []}})
            command = host.create_command(NAME, OWNER, "/private/bundle", "/private/weights", selected, {"config": {"Env": []}})
        command.remove("--interactive")
        self.assertEqual(command, default)

    def test_guest_waits_only_after_parity_and_always_tears_down(self):
        @contextmanager
        def selected(*_):
            yield types.SimpleNamespace(deadline=0), (None, types.SimpleNamespace(deadline=0))
        app = types.SimpleNamespace(verify_layout=lambda *_: None, selected_tree=selected,
                                    inspect=lambda *_: {}, apply=lambda *_: {"stages": 5})
        for mode in ("pass", "checker-fail", "parity-fail", "timeout"):
            engine = mock.Mock(pid=99999999)
            engine.poll.return_value, engine.wait.return_value = None, 0
            parity = types.SimpleNamespace(verify_sources=lambda *_: None, LOCAL_SECONDS=30,
                run=lambda *_args, **_kwargs: {"all_cases_pass": mode != "parity-fail"})
            ack = {"ready_sha256": SHA, "report_sha256": SHA, "accepted": mode == "pass"}
            def observe(_engine, _contract, row, _started):
                row["samples"].append(sample(observer.EVENTS[len(row["samples"])]))
            with mock.patch.object(lab, "PROVIDER_SESSION", session), mock.patch.object(lab, "RUNTIME_OBSERVER", observer), \
                    mock.patch.object(lab, "GPU_PROBE", fixtures.probe), mock.patch.object(lab, "CORRECTNESS", correctness), \
                    mock.patch.object(lab, "PARITY", parity), mock.patch.object(lab, "hashlib", hashlib, create=True), \
                    mock.patch.object(lab, "tokenizer_adapter_options", return_value={}, create=True), \
                    mock.patch.object(lab, "check_scratch_mounts"), mock.patch.object(lab, "read_source", return_value=b"contract"), \
                    mock.patch.object(lab, "hardware", return_value={"fixed": True}), \
                    mock.patch.object(lab, "quiet_command", return_value=b"{}"), \
                    mock.patch.object(lab, "observe_startup"), mock.patch.object(lab, "observe_runtime", side_effect=observe), \
                    mock.patch.object(lab.subprocess, "Popen", return_value=engine), mock.patch.object(lab.os, "killpg") as kill, \
                    mock.patch.object(session, "guest_wait", return_value=ack,
                        side_effect=TimeoutError("PRIVATE") if mode == "timeout" else None) as wait:
                row = lab.application_and_request({**contract(), "paths": ["/selected"]}, {},
                                                  [None, None, None, app], {"files": []}, {})
            self.assertEqual(wait.call_count, 0 if mode == "parity-fail" else 1)
            self.assertEqual(row["functional_pass"], mode == "pass")
            self.assertTrue(row["engine_stopped"])
            self.assertNotIn("PRIVATE", json.dumps(row))
            kill.assert_called_once_with(engine.pid, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
