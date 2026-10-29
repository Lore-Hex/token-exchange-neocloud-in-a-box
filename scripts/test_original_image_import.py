# SPDX-License-Identifier: Apache-2.0
"""Fake-only import contracts. Never use Docker, proc, sockets, signals or forks."""

from contextlib import contextmanager, nullcontext, ExitStack
import copy
import importlib.util
import io
import json
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).absolute().parents[1]
spec = importlib.util.spec_from_file_location("original_image_import", ROOT / "host/import-original-image.py")
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


IMAGE, OLD, CONFIG = ["sha256:" + letter * 64 for letter in "abc"]
IDENTITY = {"pid": 1234, "pgid": 1234, "session": 1234, "start_ticks": 99,
            "boot_id": "00000000-0000-0000-0000-000000000000", "cmdline_sha256": "d" * 64}


class MemoryState:
    def __init__(self, value=None):
        self.value, self.rows = value, {}
        self.fail = set()
        self.order = []

    def event(self, name, value):
        self.order.append(name)
        if name in self.fail:
            raise OSError("synthetic disk failure")
        if name in self.rows:
            raise FileExistsError(name)
        self.rows[name] = copy.deepcopy(value)

    write = event

    def event_read(self, name):
        return copy.deepcopy(self.rows[name])

    def has(self, name):
        return name in self.rows

    def claimed(self):
        return self.value, self.rows.get("claim.json", {"boot_id": IDENTITY["boot_id"], "launch_expires": 30})

    def lock(self):
        return nullcontext()


class FakeHelper:
    encoded = staticmethod(encoded)
    strict_json = staticmethod(json.loads)
    MAX_ARCHIVE = 10 << 30
    MEMORY, SECONDS = 2 << 30, 1800
    SCHEMA, PROFILE = "nib.selected-image-archive-verification/v1", "original-long1m-graphs"
    HELPERS, LIMITS = {"synthetic.py": "c" * 64}, {"archive_bytes": 10 << 30}

    def __init__(self):
        self.files = {ROOT / importer.SELF: b"synthetic importer"}
        self.info = SimpleNamespace(st_size=1024)
        self.archive_identity = [1, 2, 3, 4, 5, 1024, 7, 8]

    def read(self, path, limit, **_):
        return self.files[Path(path)]

    def identity(self, _):
        return tuple(self.archive_identity)

    @contextmanager
    def regular(self, path, **_):
        yield io.BytesIO(b"synthetic archive"), self.info


def fixture():
    helper = FakeHelper()
    value = {"schema": importer.REQUEST, "operation": "1" * 32, "producer": "2" * 40,
        "importer_sha256": importer.sha(b"synthetic importer"), "image_producer": "3" * 40,
        "verification": {"path": "/private/verification.json", "sha256": "4" * 64},
        "archive": {"path": "/private/image.tar", "sha256": "5" * 64, "bytes": 1024},
        "preparation": {"path": "/private/preparation", "sha256": "6" * 64},
        "build": {"path": "/private/build.json", "sha256": "7" * 64},
        "preserve_image_id": OLD,
        "target": {"boot_id": IDENTITY["boot_id"], "daemon_pid": 99, "daemon_start_ticks": 12,
            "daemon_cmdline_sha256": "8" * 64, "daemon_exe_sha256": "9" * 64,
            "docker_cli_sha256": "a" * 64, "daemon_id": "synthetic-daemon",
            "docker_root": "/private/docker", "containerd_root": "/private/containerd",
            "socket_device": 1, "socket_inode": 2}}
    config = {"User": "65532:65532", "Labels": {"public-label": "synthetic"},
              "Entrypoint": ["/usr/bin/python3.12", "--synthetic"], "Cmd": []}
    archive = {"image_id": IMAGE, "config_id": CONFIG, "bytes": 1024, "expanded_bytes": 8192,
        "sha256": "5" * 64, "layers": 2, "diff_ids": [CONFIG, CONFIG],
        "load_ids": [IMAGE, CONFIG], "platform": "linux/amd64"}
    source = {"build_producer": value["image_producer"]}
    admitted = {"archive": archive, "source": source, "config": config,
                "archive_identity": helper.archive_identity.copy()}
    state = MemoryState(value)
    return helper, value, admitted, state


class FakeDocker:
    def __init__(self, admitted, state):
        self.calls, self.admitted, self.state = [], admitted, state
        self.exit_code, self.polls = 0, 0
        self.target_changed, self.old_changed = False, False
        self.image_change = lambda row: None
        self.ids = {OLD}
        self.on_poll = lambda: None

    def target(self, _):
        self.calls.append("target")
        return {"identity": "changed" if self.target_changed and "load" in self.calls else "expected"}

    def inspect(self, identifier, _):
        self.calls.append(("inspect", identifier))
        if identifier == OLD:
            return {"Id": OLD, "Config": {"synthetic": "changed" if self.old_changed and "load" in self.calls else "old"}}
        row = {"Id": IMAGE, "Os": "linux", "Architecture": "amd64", "RepoTags": None,
               "Config": copy.deepcopy(self.admitted["config"]),
               "RootFS": {"Type": "layers", "Layers": self.admitted["archive"]["diff_ids"].copy()}}
        self.image_change(row)
        return row

    def inventory(self, _):
        self.calls.append("inventory")
        return self.ids

    def load(self, stream):
        assert "load-intent.json" in self.state.rows
        assert stream.tell() == 0
        self.calls.append("load")
        return SimpleNamespace(pid=1234, poll=self.poll, wait=lambda: self.exit_code)

    def poll(self):
        self.on_poll()
        self.polls += 1
        return None if self.polls == 1 else self.exit_code


class ImportTests(unittest.TestCase):
    def setUp(self):
        importer.CANCELLED = False
        self.helper, self.value, self.admitted, self.state = fixture()
        self.docker = FakeDocker(self.admitted, self.state)
        self.now = 0
        self.addCleanup(setattr, importer, "CANCELLED", False)
        # A missed injection must fail, not execute an external program or proc read.
        for owner, name in ((importer.subprocess, "Popen"), (importer, "kernel")):
            guard = mock.patch.object(owner, name, side_effect=AssertionError("no host observation or tools"))
            guard.start()
            self.addCleanup(guard.stop)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def run_load(self):
        space = [{"device": 1, "inode": 2, "required_bytes": 4096}]
        with mock.patch.object(importer, "capacity", return_value=space), \
             mock.patch.object(importer, "process", return_value=IDENTITY):
            return importer.load_once(self.value, self.admitted, self.helper, self.state, self.docker,
                                      clock=self.clock, sleep=self.sleep)

    def test_exact_request_and_source_pin(self):
        raw = encoded(self.value)
        self.assertEqual(importer.request(raw, importer.sha(raw), self.helper), self.value)
        changes = [("producer", "main"), ("operation", "1" * 31), ("importer_sha256", "0" * 64),
                   ("schema", "other"), ("unexpected", True)]
        for key, value in changes:
            row = {**self.value, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                importer.request(encoded(row), importer.sha(encoded(row)), self.helper)
        with self.assertRaises(ValueError):
            importer.request(raw, "0" * 64, self.helper)

    def test_noncanonical_path_size_boolean_and_target_rejected(self):
        for mutate in (lambda row: row["archive"].update(bytes=True),
                       lambda row: row["build"].update(path="/private/../build.json"),
                       lambda row: row["target"].update(socket_inode=True),
                       lambda row: row["target"].update(daemon_id="tcp://example.invalid")):
            row = copy.deepcopy(self.value)
            mutate(row)
            with self.assertRaises(ValueError):
                importer.request(encoded(row), importer.sha(encoded(row)), self.helper)

    def test_one_load_exact_readback_and_preserved_old_image(self):
        result = self.run_load()
        self.assertTrue(result["image_loaded"])
        self.assertFalse(result["daemon_config_bytes_rehashed"])
        self.assertEqual(self.docker.calls.count("load"), 1)
        self.assertEqual(self.state.rows["daemon-complete.json"]["daemon_completion"], "confirmed")
        self.assertLess(self.state.order.index("load-intent.json"), self.state.order.index("client.json"))

    def test_abort_before_dispatch_never_loads(self):
        self.state.rows["abort.json"] = {"abort": True}
        with self.assertRaises(ValueError):
            self.run_load()
        self.assertNotIn("load", self.docker.calls)

    def test_failed_intent_write_never_loads(self):
        self.state.fail.add("load-intent.json")
        with self.assertRaises(OSError):
            self.run_load()
        self.assertNotIn("load", self.docker.calls)

    def test_known_or_foreign_existing_image_is_not_adopted(self):
        for identifier in (IMAGE, CONFIG):
            self.docker.ids.add(identifier)
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.run_load()
            self.assertNotIn("load", self.docker.calls)

    def test_timeout_keeps_observing_without_signal_or_retry(self):
        self.docker.on_poll = lambda: setattr(self, "now", importer.LOAD_SECONDS + 1)
        with mock.patch.object(importer.os, "kill", side_effect=AssertionError("no signals")), \
             mock.patch.object(importer.os, "killpg", side_effect=AssertionError("no group signals")):
            with self.assertRaises(ValueError):
                self.run_load()
        self.assertGreaterEqual(self.docker.polls, 2)
        self.assertTrue(self.state.rows["deadline.json"]["deadline_exceeded"])
        self.assertEqual(self.state.rows["daemon-complete.json"]["daemon_completion"], "confirmed")
        self.assertEqual(self.docker.calls.count("load"), 1)

    def test_cancel_after_dispatch_still_waits_for_owned_client(self):
        self.docker.on_poll = lambda: self.state.rows.update({"abort.json": {"abort": True}})
        with self.assertRaises(ValueError):
            self.run_load()
        self.assertEqual(self.docker.calls.count("load"), 1)
        self.assertIn("client-exit.json", self.state.rows)

    def test_nonzero_client_is_unknown_not_daemon_absence(self):
        self.docker.exit_code = 1
        with self.assertRaises(ValueError):
            self.run_load()
        self.assertEqual(self.state.rows["client-exit.json"]["daemon_completion"], "unknown")
        self.assertNotIn(("inspect", IMAGE), self.docker.calls)

    def test_client_record_failure_still_waits_and_cannot_pass(self):
        self.state.fail.add("client.json")
        with self.assertRaises(ValueError):
            self.run_load()
        self.assertIn("client-exit.json", self.state.rows)
        self.assertEqual(self.docker.calls.count("load"), 1)

    def test_foreign_daemon_or_changed_preserved_image_blocks_pass(self):
        for name in ("target_changed", "old_changed"):
            self.helper, self.value, self.admitted, self.state = fixture()
            self.docker = FakeDocker(self.admitted, self.state)
            setattr(self.docker, name, True)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.run_load()
            if name == "target_changed":
                self.assertNotIn("daemon-complete.json", self.state.rows)

    def test_full_image_config_platform_labels_and_ordered_layers(self):
        mutations = [lambda row: row.update(Id=OLD), lambda row: row.update(Architecture="arm64"),
            lambda row: row.update(RepoTags=["foreign:tag"]),
            lambda row: row["Config"].update(Labels={"foreign": "label"}),
            lambda row: row["Config"].update(User="0"),
            lambda row: row["Config"].update(UnknownNonempty="unexpected"),
            lambda row: row["RootFS"].update(Layers=[CONFIG])]
        for mutate in mutations:
            self.helper, self.value, self.admitted, self.state = fixture()
            self.docker = FakeDocker(self.admitted, self.state)
            self.docker.image_change = mutate
            with self.subTest(mutation=mutations.index(mutate)), self.assertRaises(ValueError):
                self.run_load()

    def test_only_empty_config_serialization_differences_are_normalized(self):
        self.docker.image_change = lambda row: row["Config"].update(Cmd=None, OnBuild=None, Hostname="", AttachStdin=False)
        self.assertTrue(self.run_load()["image_loaded"])

    def test_unknown_false_null_and_empty_config_fields_are_not_dropped(self):
        for value in (False, None, "", [], {}):
            for side in ("readback", "archive"):
                self.helper, self.value, self.admitted, self.state = fixture()
                self.docker = FakeDocker(self.admitted, self.state)
                if side == "readback":
                    self.docker.image_change = lambda row: row["Config"].update(Unknown=value)
                else:
                    self.admitted["config"]["Unknown"] = value
                    self.docker.image_change = lambda row: row["Config"].pop("Unknown")
                with self.subTest(value=value, side=side), self.assertRaises(ValueError):
                    self.run_load()

    def test_known_config_defaults_reject_wrong_types_on_either_side(self):
        cases = (("AttachStdin", 0), ("AttachStdin", None), ("Hostname", False),
                 ("Hostname", None), ("Cmd", False), ("Cmd", {}), ("Cmd", [1]),
                 ("Volumes", []), ("Volumes", {"/data": None}), ("Labels", False),
                 ("Labels", {"label": 1}), ("Healthcheck", []), ("StopTimeout", False))
        for key, value in cases:
            for side in ("readback", "archive"):
                self.helper, self.value, self.admitted, self.state = fixture()
                self.docker = FakeDocker(self.admitted, self.state)
                if side == "readback":
                    self.docker.image_change = lambda row: row["Config"].update({key: value})
                else:
                    original = copy.deepcopy(self.admitted["config"])
                    self.admitted["config"][key] = value
                    self.docker.image_change = lambda row: row.update(Config=original)
                with self.subTest(key=key, value=value, side=side), self.assertRaises(ValueError):
                    self.run_load()

    def test_config_canonical_comparison_distinguishes_boolean_and_integer(self):
        for expected, observed in ((True, 1), (False, 0), (1, True), (0, False)):
            for wrap in (lambda value: value, lambda value: {"nested": [value]}):
                self.helper, self.value, self.admitted, self.state = fixture()
                self.admitted["config"]["RetainedField"] = wrap(expected)
                self.docker = FakeDocker(self.admitted, self.state)
                self.docker.image_change = lambda row: row["Config"].update(RetainedField=wrap(observed))
                with self.subTest(expected=expected, observed=observed, nested=wrap(True)), self.assertRaises(ValueError):
                    self.run_load()

    def test_pointer_config_defaults_do_not_erase_explicit_values(self):
        for key, value in (("StopTimeout", 0), ("Healthcheck", {})):
            self.helper, self.value, self.admitted, self.state = fixture()
            self.docker = FakeDocker(self.admitted, self.state)
            self.docker.image_change = lambda row: row["Config"].update({key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.run_load()

    def test_known_collection_and_pointer_null_defaults_are_normalized(self):
        self.admitted["config"].update(Volumes={}, ExposedPorts={}, Shell=[])
        self.docker.image_change = lambda row: row["Config"].update(
            Volumes=None, ExposedPorts=None, Shell=None, Healthcheck=None, StopTimeout=None)
        self.assertTrue(self.run_load()["image_loaded"])

    def test_config_canonical_comparison_preserves_values_but_not_key_order(self):
        self.admitted["config"]["RetainedField"] = {"first": [True, 1, None], "second": False}
        self.docker.image_change = lambda row: row["Config"].update(
            RetainedField={"second": False, "first": [True, 1, None]})
        self.assertTrue(self.run_load()["image_loaded"])

    def test_archive_inode_change_prevents_load(self):
        self.helper.archive_identity[1] += 1
        with self.assertRaises(ValueError):
            self.run_load()
        self.assertNotIn("load", self.docker.calls)

    def test_capacity_counts_archive_repeated_expansion_and_reserve(self):
        needed = 1024 + 2 * 8192 + importer.RESERVE
        info = SimpleNamespace(st_uid=0, st_mode=0o40700, st_dev=1, st_ino=2)
        space = SimpleNamespace(f_bavail=needed, f_frsize=1, f_favail=importer.MIN_INODES)
        helper = SimpleNamespace(directory=lambda _: 99)
        with mock.patch.object(importer.os, "fstat", return_value=info), \
             mock.patch.object(importer.os, "fstatvfs", return_value=space), \
             mock.patch.object(importer.os, "close"):
            result = importer.capacity(self.admitted["archive"], ["/one", "/two"], helper)
            self.assertEqual([row["required_bytes"] for row in result], [needed, needed])
            for key, value in (("f_bavail", needed - 1), ("f_favail", importer.MIN_INODES - 1)):
                before = getattr(space, key)
                setattr(space, key, value)
                with self.subTest(key=key), self.assertRaises(ValueError):
                    importer.capacity(self.admitted["archive"], ["/one"], helper)
                setattr(space, key, before)

    def test_status_never_recovers_an_unknown_operation_by_image_presence(self):
        self.state.rows["owner.json"] = {"identity": IDENTITY}
        with mock.patch.object(importer, "process", return_value={**IDENTITY, "start_ticks": 100}):
            row = importer.status(self.state)
        self.assertEqual(row["result"], "unknown")
        self.assertEqual(row["daemon_completion"], "unknown")
        self.assertFalse(row["owner_identity_live"])
        self.assertEqual(self.docker.calls, [])

    def test_foreign_receipt_cannot_be_adopted(self):
        self.state.rows["receipt.json"] = {"schema": importer.SCHEMA, "operation": "f" * 32}
        with self.assertRaises(ValueError):
            importer.status(self.state)

    def test_cancel_is_persistent_and_idempotent_not_a_signal(self):
        with mock.patch.object(importer, "process", side_effect=AssertionError("no process lookup")):
            importer.cancel(self.state)
            importer.cancel(self.state)
        self.assertEqual(self.state.order, ["abort.json"])
        self.assertEqual(self.docker.calls, [])

    def test_process_reparenting_is_not_identity_loss_but_pid_reuse_is(self):
        self.assertNotIn("ppid", IDENTITY)
        self.state.rows["owner.json"] = {"identity": IDENTITY}
        with mock.patch.object(importer, "process", return_value=IDENTITY):
            self.assertEqual(importer.status(self.state)["result"], "pending")
        with mock.patch.object(importer, "process", return_value={**IDENTITY, "boot_id": "foreign"}):
            self.assertEqual(importer.status(self.state)["result"], "unknown")

    @contextmanager
    def launch(self):
        raw = encoded(self.value)
        self.state.expected, self.state.helper, self.state.fd = importer.sha(raw), self.helper, 77
        identity = IDENTITY.copy()
        def spawn(command, **options):
            self.assertIn("claim.json", self.state.rows)
            self.assertTrue(options["start_new_session"])
            self.assertEqual(options["pass_fds"], (11,))
            identity["cmdline_sha256"] = importer.sha(b"\0".join(part.encode() for part in command) + b"\0")
            return SimpleNamespace(pid=identity["pid"])
        def permit(fd, data):
            self.assertIn("owner.json", self.state.rows)
            self.assertEqual((fd, data), (12, b"1"))
            return 1
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(importer, "boot", return_value=IDENTITY["boot_id"]))
            stack.enter_context(mock.patch.object(importer, "process", side_effect=lambda _: identity.copy()))
            stack.enter_context(mock.patch.object(importer.time, "monotonic", side_effect=self.clock))
            stack.enter_context(mock.patch.object(importer.os, "scandir", return_value=nullcontext(iter([]))))
            for name, result in (("open", 10), ("pipe", (11, 12)), ("close", None), ("fsync", None)):
                stack.enter_context(mock.patch.object(importer.os, name, return_value=result))
            child = stack.enter_context(mock.patch.object(importer.subprocess, "Popen", side_effect=spawn))
            write = stack.enter_context(mock.patch.object(importer.os, "write", side_effect=permit))
            yield raw, child, write

    def test_owner_publication_precedes_pipe_permit(self):
        with self.launch() as (raw, child, write):
            result = importer.start(self.state, raw)
        self.assertEqual(result["result"], "pending")
        child.assert_called_once()
        write.assert_called_once_with(12, b"1")

    def test_claim_or_directory_sync_failure_never_spawns(self):
        self.state.fail.add("claim.json")
        with self.launch() as (raw, child, write):
            with self.assertRaises(OSError):
                importer.start(self.state, raw)
            child.assert_not_called()
            write.assert_not_called()
        self.state = MemoryState(self.value)
        with self.launch() as (raw, child, write), mock.patch.object(importer.os, "fsync", side_effect=OSError):
            with self.assertRaises(OSError):
                importer.start(self.state, raw)
            child.assert_not_called()
            write.assert_not_called()

    def test_owner_record_failure_never_permits_work(self):
        self.state.fail.add("owner.json")
        with self.launch() as (raw, child, write):
            with self.assertRaises(OSError):
                importer.start(self.state, raw)
            child.assert_called_once()
            write.assert_not_called()

    def test_expiry_crossing_publication_prevents_permit(self):
        event = self.state.event
        def delayed(name, row):
            event(name, row)
            if name == "owner.json":
                self.now = 31
        self.state.event = delayed
        with self.launch() as (raw, child, write):
            with self.assertRaises(ValueError):
                importer.start(self.state, raw)
            child.assert_called_once()
            write.assert_not_called()

    def test_existing_state_is_never_replayed(self):
        with self.launch() as (raw, child, write), \
             mock.patch.object(importer.os, "scandir", return_value=nullcontext(iter(["request.json"]))):
            with self.assertRaises(ValueError):
                importer.start(self.state, raw)
            child.assert_not_called()
            write.assert_not_called()

    def test_worker_rechecks_expiry_after_owner_identity_io(self):
        self.state.rows["owner.json"] = {"identity": IDENTITY}
        def delayed(_):
            self.now = 31
            return IDENTITY
        with mock.patch.object(importer.signal, "signal"), \
             mock.patch.object(importer.select, "select", return_value=([11], [], [])), \
             mock.patch.object(importer.os, "read", return_value=b"1"), \
             mock.patch.object(importer.os, "close"), \
             mock.patch.object(importer, "boot", return_value=IDENTITY["boot_id"]), \
             mock.patch.object(importer, "process", side_effect=delayed), \
             mock.patch.object(importer.time, "monotonic", side_effect=self.clock), \
             mock.patch.object(importer, "admit_inputs") as verify:
            with self.assertRaises(ValueError):
                importer.worker(self.state, 11)
            verify.assert_not_called()

    def test_mutating_command_is_only_untagged_load_without_timeout_runner(self):
        backend = importer.Docker.__new__(importer.Docker)
        stream = io.BytesIO(b"synthetic")
        with mock.patch.object(importer.subprocess, "Popen") as launch:
            backend.load(stream)
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/docker", "image", "load", "--quiet"])
        options = launch.call_args.kwargs
        self.assertIs(options["stdin"], stream)
        self.assertTrue(options["start_new_session"])
        self.assertEqual(options["env"], importer.ENV)
        self.assertEqual(options["stdout"], importer.subprocess.DEVNULL)
        self.assertEqual(options["stderr"], importer.subprocess.DEVNULL)
        self.assertNotIn("timeout", options)

    def test_terminal_cancel_does_not_rewrite_or_relabel_success(self):
        row = {"schema": importer.SCHEMA, "operation": self.value["operation"],
            "producer": self.value["producer"], "script_sha256": self.value["importer_sha256"],
            "verifier_sha256": importer.VERIFIER_SHA, "result": "pass", "image_loaded": True,
            "readback_verified": True, "preserved_image_unchanged": True, "daemon_completion": "confirmed",
            **{key: False for key in importer.FALSE_FLAGS}}
        self.state.rows["receipt.json"] = row
        self.assertEqual(importer.cancel(self.state), row)
        self.assertNotIn("abort.json", self.state.rows)

    def test_daemon_platform_backend_and_identity_are_closed(self):
        backend = importer.Docker.__new__(importer.Docker)
        backend.helper, backend.value = self.helper, self.value
        row = {"ID": "synthetic-daemon", "ServerVersion": "29.1.3", "OSType": "linux",
            "DockerRootDir": "/private/docker", "Driver": "overlayfs",
            "DriverStatus": [["driver-type", "io.containerd.snapshotter.v1"]]}
        backend.command = lambda *_: encoded(row)
        proc = {**IDENTITY, "pid": 99, "start_ticks": 12, "cmdline_sha256": "8" * 64}
        inode = SimpleNamespace(st_dev=1, st_ino=2, st_ctime_ns=123, st_uid=0,
                                st_mode=stat.S_IFSOCK | 0o600)
        with mock.patch.object(importer, "boot", return_value=IDENTITY["boot_id"]), \
             mock.patch.object(importer, "process", return_value=proc), \
             mock.patch.object(importer.os, "readlink", return_value="/usr/bin/dockerd"), \
             mock.patch.object(importer.os, "stat", return_value=inode), \
             mock.patch.object(importer.os, "lstat", return_value=inode), \
             mock.patch.object(importer, "executable", return_value=[1, 2]):
            self.assertEqual(backend.target(float("inf"))["daemon"], proc)
            for key, value in (("ID", "foreign"), ("ServerVersion", "other"), ("OSType", "windows"),
                               ("DockerRootDir", "/other"), ("DriverStatus", [])):
                old = row[key]
                row[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    backend.target(float("inf"))
                row[key] = old
            inode.st_ino = 3
            with self.assertRaises(ValueError):
                backend.target(float("inf"))


class InputTests(unittest.TestCase):
    def setUp(self):
        importer.CANCELLED = False
        for owner, name in ((importer.subprocess, "Popen"), (importer, "kernel")):
            guard = mock.patch.object(owner, name, side_effect=AssertionError("no host observation or tools"))
            guard.start()
            self.addCleanup(guard.stop)
        self.helper, self.value, self.admitted, self.state = fixture()
        self.receipt = {"schema": self.helper.SCHEMA, "result": "pass", "producer": self.value["image_producer"],
            "script_sha256": importer.VERIFIER_SHA, "helpers": self.helper.HELPERS, "limits": self.helper.LIMITS,
            "profile": self.helper.PROFILE, "archive_verified": True, "worker_exit_confirmed": True,
            "image_loaded": False, "started_utc": "synthetic", "ended_utc": "synthetic",
            **{key: False for key in importer.FALSE_FLAGS}, "source": self.admitted["source"],
            "archive": self.admitted["archive"], "expected": {
                "archive_sha256": self.value["archive"]["sha256"], "archive_bytes": 1024,
                "build_sha256": self.value["build"]["sha256"], "preparation_sha256": self.value["preparation"]["sha256"]}}
        self.helper.source_inputs = lambda _: ({"image_id": IMAGE}, self.admitted["source"])
        def verify(*_):
            self.helper.strict_json(encoded({"os": "linux", "architecture": "amd64", "config": self.admitted["config"]}))
            return self.admitted["archive"]
        self.helper.verify_archive = mock.Mock(side_effect=verify)

    def admit(self):
        raw = encoded(self.receipt)
        self.value["verification"]["sha256"] = importer.sha(raw)
        self.helper.files[Path(self.value["verification"]["path"])] = raw
        return importer.admit_inputs(self.value, self.helper, self.state, float("inf"))

    def test_full_original_join_and_existing_parser_observation(self):
        result = self.admit()
        self.assertEqual(result, self.admitted)
        self.helper.verify_archive.assert_called_once()
        self.assertIs(self.helper.strict_json, json.loads)

    def test_false_or_foreign_receipt_never_reaches_parser(self):
        for key, value in (("result", "fail"), ("archive_verified", False), ("worker_exit_confirmed", False),
                           ("script_sha256", "0" * 64), ("producer", "0" * 40),
                           ("profile", "other"), ("helpers", {}), ("limits", {}),
                           *((flag, True) for flag in importer.FALSE_FLAGS)):
            original = self.receipt[key]
            self.receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.admit()
            self.receipt[key] = original
        self.helper.verify_archive.assert_not_called()

    def test_cross_build_preparation_and_archive_joins_rejected(self):
        for key in self.receipt["expected"]:
            old = self.receipt["expected"][key]
            self.receipt["expected"][key] = 1 if type(old) is int else "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.admit()
            self.receipt["expected"][key] = old
        self.helper.verify_archive.assert_not_called()

    def test_reverified_expansion_or_diffids_must_match_exact_receipt(self):
        for key, value in (("expanded_bytes", 1), ("diff_ids", []), ("config_id", OLD)):
            original = copy.deepcopy(self.receipt["archive"])
            self.receipt["archive"] = {**original, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.admit()
            self.receipt["archive"] = original

    def test_shared_verifier_pin_and_original_parser_are_unchanged(self):
        raw = (ROOT / importer.VERIFIER).read_bytes()
        self.assertEqual(importer.sha(raw), importer.VERIFIER_SHA)
        self.assertEqual(importer.sha((ROOT / "scripts/pilot/export.py").read_bytes()),
                         "baaf98e34904b080513fdd468e83e1eb818c2e2ce69d87cf6fb6a421e7268666")


if __name__ == "__main__":
    unittest.main()
