# SPDX-License-Identifier: Apache-2.0
"""Synthetic private CLI, daemon, watchdog and receipts; no sockets or GPU."""

from contextlib import contextmanager, ExitStack, redirect_stdout
import io
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

import test_selected_service_smoke as smoke_fixture
import test_selected_combined_owner as owner_fixture

SHA, owner, receipt, service = owner_fixture.SHA, owner_fixture.owner, owner_fixture.receipt, owner_fixture.service

smoke = smoke_fixture.smoke
path = Path(__file__).absolute().parents[1] / "host/record-selected-combined.py"
host = types.ModuleType("combined_host_test")
host.__file__ = str(path)
exec(compile(path.read_bytes(), str(path), "exec"), host.__dict__)


class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_fixture.SmokeTests.setUpClass()
        cls.child, _ = owner_fixture.OwnerTests().execute()

    def setUp(self):
        self.fixture = smoke_fixture.SmokeTests()
        self.fixture.setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.args = self.fixture.args
        self.args.receipt_directory = str(self.directory)
        self.args.preparation_seconds = 300
        self.args.hard_stop_at = self.fixture.clock.timestamp(10000)
        self.fixture.build["prepared_sha256"] = SHA
        self.fixture.image["Config"]["Entrypoint"][-1] = SHA
        self.raw = receipt.encoded(self.child) + b"\n"

    def execute(self, fault=None):
        fixture = self.fixture
        sources = {"scripts/synthetic.py": b"source-bound bytes"}
        @contextmanager
        def admitted(args, result, **kwargs):
            self.assertEqual(kwargs, {"lifetime_profile": service.ORDINARY_PROFILE})
            contract = service.Contract.begin(args.hard_stop_at, input_price="1", output_price="2",
                lifetime_profile=service.ORDINARY_PROFILE, execution_profile=owner.BASE_PROFILE)
            result.update(preflight_pass=True, reason="not-run", prepared_sha256=SHA,
                build_sha256=args.build_sha256, preparation_sha256=args.preparation_sha256)
            yield (contract, fixture.lab, fixture.recorder, fixture.lab.controlled_env(args.docker_host),
                   fixture.build, fixture.base, fixture.image, Path("/private/stage"))
            if fault == "input-cleanup":
                raise OSError("PRIVATE_CANARY")
        def attach(command, **kwargs):
            fixture.commands.append(command)
            self.assertEqual(command[:3], ["docker", "container", "start"])
            self.assertLessEqual(kwargs["timeout"], 8740 - 120)
            self.assertEqual(kwargs["max_output_bytes"], receipt.MAX_CAPTURE)
            kwargs["stdout_file"].write(self.raw)
            if fault == "attach-after-payload":
                raise subprocess.TimeoutExpired(command, 1)
            fixture.info["State"].update(Status="exited", Running=False)
            if fault == "exit-after-payload":
                fixture.info["State"]["ExitCode"] = 1
            return types.SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(self.args, "hard_stop_at", fixture.clock.timestamp(3000)), \
                fixture.running(failure="cleanup" if fault == "cleanup" else None,
                             monitor_code=1 if fault == "watchdog" else 0), ExitStack() as stack:
            self.args.hard_stop_at = fixture.clock.timestamp(10000)
            stack.enter_context(mock.patch.object(host, "smoke_module", return_value=smoke))
            stack.enter_context(mock.patch.object(smoke, "admitted_runtime", side_effect=admitted))
            stack.enter_context(mock.patch.object(smoke.image_builder, "committed_sources", return_value=("source", sources)))
            closure = {"context/service/" + k: v for k, v in sources.items()}
            if fault == "source":
                closure["context/service/scripts/synthetic.py"] = b"other source"
            stack.enter_context(mock.patch.object(smoke.image_builder, "validated_files", return_value=({}, closure, {})))
            stack.enter_context(mock.patch.object(fixture.recorder.snapshot, "execute_process_group", side_effect=attach))
            return host.execute(self.args)

    def test_one_owned_lifetime_original_capture_and_host_replay(self):
        row = self.execute()
        self.assertEqual(row["result"], "pass")
        self.assertIsNone(self.fixture.info)
        self.assertEqual((self.directory / "capture.jsonl").read_bytes(), self.raw)
        replay = receipt.replay_directory(self.directory, SHA)
        self.assertEqual(replay["result"], "pass")
        create = [c for c in self.fixture.commands if c[2] == "create"]
        self.assertEqual(len(create), 1)
        self.assertEqual(len([c for c in self.fixture.commands if c[2] == "start"]), 1)
        command = create[0]
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index(self.fixture.build["image_id"]) + 1], "--combined-qualification")
        self.assertEqual(command[command.index("--original-hard-wall") + 1], str(self.fixture.clock.wall + 10000))
        self.assertEqual(command[command.index("--hard-stop-at") + 1], self.fixture.clock.timestamp(8560))

    def test_failure_after_payload_never_passes_but_preserves_original_and_cleanup(self):
        for fault in ("attach-after-payload", "exit-after-payload", "cleanup", "watchdog", "input-cleanup"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as root:
                self.args.receipt_directory = root
                row = self.execute(fault)
                self.assertEqual(row["result"], "fail")
                self.assertEqual((Path(root) / "capture.jsonl").read_bytes(), self.raw)
                stored = receipt.decode((Path(root) / "host.json").read_bytes(), receipt.MAX_CAPTURE + 16384)
                self.assertNotIn("PRIVATE_CANARY", receipt.encoded(stored).decode())
                if fault == "attach-after-payload":
                    self.assertFalse(stored["capture_complete"])
                    with self.assertRaises(ValueError): receipt.replay_directory(root, SHA)
                else:
                    self.assertEqual(receipt.replay_directory(root, SHA)["result"], "fail")

    def test_source_mismatch_no_create_and_existing_originals_not_overwritten(self):
        row = self.execute("source")
        self.assertEqual(row["result"], "fail")
        self.assertFalse(any(c[2] == "create" for c in self.fixture.commands))
        original = (self.directory / "host.json").read_bytes()
        with self.assertRaises(ValueError): self.execute()
        self.assertEqual((self.directory / "host.json").read_bytes(), original)

    def test_replay_rejects_mixed_host_intent_capture_and_nonprivate_files(self):
        self.execute()
        path = self.directory / "host.json"
        original = path.read_bytes()
        for key, value in (("capture_sha256", "c" * 64), ("prepared_sha256", "b" * 64),
                           ("build_sha256", "d" * 64), ("lifecycle", {}), ("deadline_plan", {})):
            row = receipt.decode(original, receipt.MAX_CAPTURE + 16384)
            row[key] = value
            path.write_bytes(receipt.encoded(row))
            with self.subTest(key=key), self.assertRaises(ValueError):
                receipt.replay_directory(self.directory, SHA)
        path.write_bytes(original)
        path.chmod(0o644)
        with self.assertRaises(ValueError): receipt.replay_directory(self.directory, SHA)
        path.chmod(0o600)
        (self.directory / "capture.jsonl").unlink()
        with self.assertRaises(OSError): receipt.replay_directory(self.directory, SHA)

    def test_exclusive_payload_fsync_failure_retains_original_bytes(self):
        path = self.directory / "partial.json"
        raw = b'{"original":"synthetic"}'
        with mock.patch.object(receipt.os, "fsync", side_effect=OSError("PRIVATE_CANARY")), self.assertRaises(OSError):
            receipt.retain(path, raw, 128)
        self.assertEqual(path.read_bytes(), raw)
        with self.assertRaises(FileExistsError): receipt.retain(path, b"replacement", 128)

    def test_cli_replay_is_content_free_and_requires_explicit_prepared_identity(self):
        self.execute()
        output = io.StringIO()
        with redirect_stdout(output):
            code = host.main(["--replay", "--receipt-directory", str(self.directory), "--prepared-sha256", SHA])
        self.assertEqual(code, 0)
        self.assertLess(len(output.getvalue()), 1024)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(host.main(["--replay", "--receipt-directory", str(self.directory)]), 1)


if __name__ == "__main__":
    unittest.main()
