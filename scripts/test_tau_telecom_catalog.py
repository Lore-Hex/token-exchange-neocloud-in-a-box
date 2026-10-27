# SPDX-License-Identifier: Apache-2.0
"""Offline binding tests; real-source reproduction is an explicit local check."""

import copy
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import time
import types
import unittest
from unittest import mock

import tau_telecom_catalog as catalog
import test_tau_telecom_projection as payloads
import test_tau_trial as containers

spec = importlib.util.spec_from_file_location("telecom_recorder", Path(__file__).with_name("record-tau-telecom-catalog.py"))
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
tau, selection = catalog.tau, catalog.selection
BUDGET = {"weekly_cap_usd": "10000", "gpu_committed_usd": "9534.336",
          "cpu_disk_reserved_usd": "0", "other_committed_usd": "50"}  # Synthetic reservation inputs.


def synthetic():
    value = catalog.catalog(time.monotonic() + 10)
    tasks = [{"id": name, "user_tools": None, "evaluation_criteria": {"nl_assertions": []}}
             for name in value["selected_tasks"]]
    names = selection.source_names(["telecom"])
    sources = dict(zip(names, (tau.encoded(tasks), tau.encoded({"base": [t["id"] for t in tasks]}))))
    pins = {"revision": tau.REVISION, "files": {name: {"bytes": len(raw), "sha256": tau.digest(raw),
            "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}
            for name, raw in sources.items()}}
    return sources, pins


@contextmanager
def bound_fixture():
    sources, pins = synthetic()
    plan = selection.build(sources, pins, "a" * 40, ["telecom"], 1, 42, BUDGET)
    raw_pins, original = tau.encoded(pins), catalog.runtime.read_file
    def read(path, limit, deadline):
        if path == catalog.runtime.ROOT / tau.PINS:
            return raw_pins
        if path == Path("/synthetic/archive"):
            return b"synthetic-archive"
        return original(path, limit, deadline)
    def source(raw, actual_pins, deadline):
        if raw != b"synthetic-archive" or actual_pins != pins:
            raise ValueError("synthetic source rejected")
        return {}, sources
    with mock.patch.object(tau, "PINS_SHA", tau.digest(raw_pins)), \
            mock.patch.object(catalog.runtime, "read_file", side_effect=read), \
            mock.patch.object(catalog.runtime, "source_inputs", side_effect=source):
        stage = {"plan": plan, "index": 0}
        yield catalog.bind(stage, Path("/synthetic/archive"), time.monotonic() + 30)


def body():
    value = payloads.body()
    value["tools"] = catalog.catalog(time.monotonic() + 10)["tools"]
    call = value["messages"][2]["tool_calls"][0]
    call["name"] = call["function"]["name"] = "check_status_bar"
    call["function"]["arguments"] = " { } "
    return value


class CatalogTests(unittest.TestCase):
    def test_pinned_catalog_has_original_order_and_schema(self):
        value = catalog.catalog(time.monotonic() + 10)
        self.assertEqual(value["upstream_revision"], catalog.runtime.REVISION)
        self.assertEqual(value["source_archive_sha256"], catalog.runtime.ARCHIVE_SHA)
        self.assertEqual(value["source_tree"], catalog.runtime.SOURCE_TREE)
        self.assertEqual(value["runtime_lock_sha256"], catalog.runtime.CATALOG_SHA)
        self.assertEqual(value["target"], catalog.runtime.TARGET)
        self.assertEqual(value["tool_count"], 30)
        self.assertEqual(value["base_task_count"], 114)
        self.assertEqual(len(value["selected_tasks"]), 5)
        self.assertEqual(value["tools"][0]["function"]["name"], "check_status_bar")
        self.assertEqual(value["tools"][-1]["function"]["name"], "make_payment")
        self.assertEqual(tau.digest(tau.encoded(value["tools"])), value["tool_catalog_sha256"])

    def test_catalog_tampering_is_not_authenticated(self):
        raw = tau.encoded(catalog.catalog(time.monotonic() + 10))
        for changed in (raw + b" ", raw.replace(b"check_status_bar", b"changed_tool")):
            with mock.patch.object(catalog.runtime, "read_file", return_value=changed), self.assertRaises(ValueError):
                catalog.catalog(time.monotonic() + 10)

    def test_binding_preserves_original_stage_budget_and_closed_flags(self):
        with bound_fixture() as contract:
            frozen = copy.deepcopy(contract)
            self.assertEqual(catalog.validate(contract, Path("/synthetic/archive"), time.monotonic() + 10), frozen)
            self.assertEqual(contract, frozen)
            self.assertTrue(contract["source_authenticated"])
            for key in ("execution_authorized", "live_admission", "token_count_parity_verified", "quality_eligible"):
                self.assertIs(contract[key], False)
            self.assertEqual(contract["stage"]["plan"]["budget"]["inputs"], BUDGET)
            self.assertEqual(contract["stage"]["plan"]["budget"]["total_usd_per_trial"], "70.044864")

    def test_contract_and_task_rebinding_rejected(self):
        with bound_fixture() as original:
            mutations = (lambda v: v.update(live_admission=True), lambda v: v.update(source_authenticated=False),
                         lambda v: v.update(catalog_sha256="0" * 64), lambda v: v.update(tool_count=16),
                         lambda v: v.update(upstream_revision="b" * 40), lambda v: v["stage"].update(index=1),
                         lambda v: v["stage"]["plan"].update(seed=99), lambda v: v.update(extra="CANARY"))
            for mutate in mutations:
                value = copy.deepcopy(original)
                mutate(value)
                with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                    catalog.validate(value, Path("/synthetic/archive"), time.monotonic() + 10)

    def test_source_verification_and_deadline_cannot_be_skipped(self):
        with bound_fixture() as contract:
            with mock.patch.object(catalog.runtime, "source_inputs", side_effect=ValueError("rejected")), \
                    self.assertRaises(ValueError):
                catalog.validate(contract, Path("/synthetic/archive"), time.monotonic() + 10)
            with self.assertRaises(ValueError):
                catalog.validate(contract, Path("/synthetic/archive"), time.monotonic() - 1)
            with mock.patch.object(selection, "verify_sources", side_effect=ValueError("rejected")), \
                    self.assertRaises(ValueError):
                catalog.validate(contract, Path("/synthetic/archive"), time.monotonic() + 10)

    def test_source_bound_projection_retains_private_semantics(self):
        with bound_fixture() as contract:
            request = body()
            frozen = copy.deepcopy(request)
            result = catalog.project(request, contract, Path("/synthetic/archive"), time.monotonic() + 10)
            self.assertEqual(request, frozen)
            expected = catalog.projection.project(request, request["tools"])
            self.assertEqual(result["chat"], expected["chat"])
            self.assertEqual(result["count"], expected["count"])
            self.assertTrue(result["source_authenticated"])
            self.assertFalse(result["token_count_parity_verified"])
            self.assertFalse(result["live_admission"])
            self.assertEqual(result["catalog_binding_sha256"], tau.digest(tau.encoded(contract)))
            self.assertEqual(result["chat"]["tools"], frozen["tools"])

    def test_changed_catalog_and_history_never_get_source_flag(self):
        with bound_fixture() as contract:
            for mutate in (lambda v: v["tools"].reverse(),
                           lambda v: v["tools"][0]["function"]["parameters"].update(required=["changed"]),
                           lambda v: v["messages"].pop(3)):
                request = body()
                mutate(request)
                with self.assertRaises(ValueError):
                    catalog.project(request, contract, Path("/synthetic/archive"), time.monotonic() + 10)

    def test_no_network_logging_or_live_admission(self):
        output = io.StringIO()
        with bound_fixture() as contract, redirect_stdout(output), redirect_stderr(output), \
                mock.patch.object(catalog.projection.external.bounded_https, "HTTPS") as http:
            catalog.project(body(), contract, Path("/synthetic/archive"), time.monotonic() + 10)
            with self.assertRaises(ValueError):
                catalog.projection.external.simulator_request(body(), full_task=True, support_chat=True)
            candidate = body()
            candidate["model"] = catalog.projection.external.policy.PUBLIC_MODEL
            with self.assertRaises(ValueError):
                catalog.projection.external.candidate_request(candidate, full_task=True)
            with self.assertRaises(ValueError):
                catalog.projection.external.policy.validate(contract)
        http.assert_not_called()
        self.assertEqual(output.getvalue(), "")

    @unittest.skipUnless(os.environ.get("NIB_TAU_SOURCE_ARCHIVE"), "explicit pinned upstream archive required")
    def test_real_pinned_source_end_to_end(self):
        path, deadline = Path(os.environ["NIB_TAU_SOURCE_ARCHIVE"]), time.monotonic() + 120
        r = catalog.runtime
        pins = tau.json_value(r.read_file(r.ROOT / tau.PINS, 1 << 20, deadline))
        _, sources = r.source_inputs(r.read_file(path, r.ARCHIVE_LIMIT, deadline), pins, deadline)
        plan = selection.build(sources, pins, "a" * 40, ["telecom"], 1, 42, BUDGET)
        for index in range(5):
            contract = catalog.bind({"plan": plan, "index": index}, path, deadline)
            projected = catalog.project(body(), contract, path, deadline)
            self.assertTrue(projected["source_authenticated"])
            self.assertEqual(projected["chat"]["tools"], body()["tools"])


class RecorderTests(unittest.TestCase):
    def test_entrypoint_is_exact_and_all_existing_isolation_checks_apply(self):
        trial, path, sha = containers.runner, Path("/private/payload"), "b" * 64
        argv = recorder.command(trial, "fixed", path, sha)
        info = containers.LifecycleTests().info(argv)
        recorder.check_container(trial, info, "fixed", path, sha, containers.selected_manifest())
        mutations = (lambda v: v["Config"]["Cmd"].append("--paid"),
                     lambda v: v["HostConfig"].update(NetworkMode="host"),
                     lambda v: v["HostConfig"].update(ReadonlyRootfs=False),
                     lambda v: v["Mounts"][0].update(RW=True),
                     lambda v: v["HostConfig"].update(Memory=0),
                     lambda v: v.update(Image="sha256:" + "f" * 64))
        for mutate in mutations:
            bad = copy.deepcopy(info)
            mutate(bad)
            with self.assertRaises(ValueError):
                recorder.check_container(trial, bad, "fixed", path, sha, containers.selected_manifest())

    def test_host_import_does_not_import_upstream_or_contact_docker(self):
        with mock.patch.object(recorder, "materialize") as extract:
            spec.loader.exec_module(recorder)
        extract.assert_not_called()

    def test_recorder_failure_cleanup_precedes_output_and_has_no_credentials(self):
        trial = containers.runner
        for failure in (None, "prepare", "cancel_prepare", "create", "inspect", "start", "cancel_start",
                        "terminal", "cleanup", "cancel_cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, \
                    socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                root = Path(directory).resolve()
                sock.bind(str(root / "docker.sock"))
                state = {"argv": None, "inspects": 0, "cleaned": False}
                def prepare(*args):
                    if failure == "cancel_prepare":
                        signal.raise_signal(signal.SIGTERM)
                    if failure == "prepare":
                        raise ValueError("PRIVATE_EXCEPTION_CANARY")
                    return {}, "b" * 64
                def capture(argv, **kwargs):
                    if argv[1] == "-C":
                        return b"a" * 40 if argv[3] == "rev-parse" else b""
                    self.assertEqual(set(kwargs["env"]), {"PATH", "HOME", "DOCKER_CONFIG", "LANG", "LC_ALL"})
                    self.assertGreater(kwargs["timeout"], 0)
                    args = argv[5:]
                    if args[0] == "image":
                        return tau.encoded([containers.image_inspection()])
                    if args[0] == "create":
                        state["argv"] = args
                        if failure == "create":
                            raise ValueError("ambiguous create")
                        return b"container-id"
                    if args[0] == "inspect":
                        state["inspects"] += 1
                        if failure == "inspect":
                            raise ValueError("inspect failed")
                        info = containers.LifecycleTests().info(state["argv"])
                        if state["inspects"] == 2 and failure == "terminal":
                            info["State"]["ExitCode"] = 1
                        return tau.encoded([info])
                    if args[0] == "start":
                        if failure == "start":
                            raise ValueError("PRIVATE_EXCEPTION_CANARY")
                        if failure in ("cancel_start", "cancel_cleanup"):
                            signal.raise_signal(signal.SIGTERM)
                        return tau.encoded(catalog.catalog(time.monotonic() + 10))
                    self.assertEqual(args[:2], ["rm", "--force"])
                    self.assertEqual(args[2], state["argv"][state["argv"].index("--name") + 1])
                    state["cleaned"] = True
                    if failure in ("cancel_start", "cancel_cleanup"):
                        signal.raise_signal(signal.SIGINT)
                    if failure in ("cleanup", "cancel_cleanup"):
                        raise ValueError("PRIVATE_EXCEPTION_CANARY")
                    return b"removed"
                try:
                    with mock.patch.object(recorder, "load", return_value=trial), \
                            mock.patch.object(recorder, "prepare", side_effect=prepare), \
                            mock.patch.object(trial, "publish", side_effect=lambda path, files: (path / "payload").mkdir(parents=True)), \
                            mock.patch.object(recorder.shutil, "which", return_value="/docker"), \
                            mock.patch.object(trial.probe, "capture", side_effect=capture), \
                            mock.patch.object(recorder, "isolated", return_value=True):
                        args = types.SimpleNamespace(source_archive=root / "archive", wheelhouse=root / "wheels",
                            docker_socket=root / "docker.sock", output=root / "output", receipt=root / "receipt.json")
                        stdout, stderr = io.StringIO(), io.StringIO()
                        handlers = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            code = recorder.main(["--source-archive", str(args.source_archive), "--wheelhouse", str(args.wheelhouse),
                                "--docker-socket", str(args.docker_socket), "--output", str(args.output), "--receipt", str(args.receipt)])
                        self.assertEqual(handlers, {kind: signal.getsignal(kind) for kind in handlers})
                        result = json.loads(args.receipt.read_bytes())
                        self.assertEqual(result["result"], "pass" if failure is None else "fail")
                        self.assertIs(result["completed"], failure is None)
                        self.assertIs(result["source_authenticated"], failure is None)
                        self.assertEqual(code, 0 if failure is None else 1)
                        self.assertEqual(result["cleanup"], "unconfirmed" if failure in ("cleanup", "cancel_cleanup")
                                         else "not_created" if failure in ("prepare", "cancel_prepare") else "owned-container-removed")
                        self.assertEqual(result["producer_git_sha"], "a" * 40)
                        self.assertEqual(result["source_archive_sha256"], trial.runtime.ARCHIVE_SHA)
                        self.assertEqual(result["stage"], {None: "completed", "prepare": "source_verification", "cancel_prepare": "source_verification",
                            "create": "container_creation", "inspect": "container_verification", "start": "extraction",
                            "cancel_start": "extraction", "terminal": "terminal_verification",
                            "cleanup": "cleanup", "cancel_cleanup": "extraction"}[failure])
                        self.assertIs(result["cancelled"], failure in ("cancel_prepare", "cancel_start", "cancel_cleanup"))
                        self.assertEqual(args.output.exists(), failure is None)
                        self.assertNotIn("PRIVATE_EXCEPTION_CANARY", stdout.getvalue() + stderr.getvalue())
                        self.assertNotIn("PRIVATE_EXCEPTION_CANARY", args.receipt.read_text())
                    self.assertEqual(state["cleaned"], failure not in ("prepare", "cancel_prepare"))
                finally:
                    sock.close()

    def test_existing_receipt_is_never_replaced_or_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            original.write_bytes(b"existing evidence")
            link = root / "link"
            link.symlink_to(original)
            for receipt in (original, link):
                args = types.SimpleNamespace(source_archive=root / "archive", wheelhouse=root / "wheels",
                    docker_socket=root / "socket", output=root / "output", receipt=receipt)
                with mock.patch.object(recorder, "isolated", return_value=True), \
                        mock.patch.object(recorder, "record") as run, \
                        redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(recorder.host(args), 1)
                run.assert_not_called()
                self.assertEqual(original.read_bytes(), b"existing evidence")
                self.assertTrue(link.is_symlink())

    def test_receipt_created_during_extraction_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = types.SimpleNamespace(source_archive=root / "archive", wheelhouse=root / "wheels",
                docker_socket=root / "socket", output=root / "output", receipt=root / "receipt.json")
            def extract(*values, progress):
                args.receipt.write_bytes(b"concurrent evidence")
                raise ValueError("PRIVATE_EXCEPTION_CANARY")
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(recorder, "isolated", return_value=True), \
                    mock.patch.object(recorder, "load", return_value=containers.runner), \
                    mock.patch.object(containers.runner.probe, "capture", return_value=b"a" * 40), \
                    mock.patch.object(recorder, "record", side_effect=extract), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(recorder.host(args), 1)
            self.assertEqual(args.receipt.read_bytes(), b"concurrent evidence")
            self.assertEqual(json.loads(stdout.getvalue())["stage"], "receipt_output")
            self.assertNotIn("PRIVATE_EXCEPTION_CANARY", stdout.getvalue() + stderr.getvalue())

    def test_provenance_failure_retains_available_identity_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            args = types.SimpleNamespace(source_archive=root / "archive", wheelhouse=root / "wheels",
                docker_socket=root / "socket", output=root / "output", receipt=root / "receipt.json")
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(recorder, "isolated", return_value=True), \
                    mock.patch.object(recorder, "load", return_value=containers.runner), \
                    mock.patch.object(containers.runner.probe, "capture", side_effect=[b"a" * 40, ValueError("PRIVATE_EXCEPTION_CANARY")]), \
                    mock.patch.object(recorder, "record") as run, \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(recorder.host(args), 1)
            run.assert_not_called()
            result = json.loads(args.receipt.read_bytes())
            self.assertEqual((result["result"], result["stage"], result["cleanup"]), ("fail", "provenance", "not_created"))
            self.assertFalse(result["completed"] or result["source_authenticated"])
            self.assertEqual(result["producer_git_sha"], "a" * 40)
            self.assertIsNone(result["producer_dirty"])
            self.assertEqual(result["source_archive_sha256"], containers.runner.runtime.ARCHIVE_SHA)
            self.assertNotIn("PRIVATE_EXCEPTION_CANARY", stdout.getvalue() + stderr.getvalue() + args.receipt.read_text())


if __name__ == "__main__":
    unittest.main()
