# SPDX-License-Identifier: Apache-2.0
"""Synthetic local packaging only; no operator inputs, image load or transport."""

from contextlib import ExitStack, contextmanager
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

import original_image_package as package

ROOT = Path(__file__).absolute().parents[1]
adapter, files, receiver, materializer, runner = package.helpers()


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        self.repository, self.preparation = self.root / "repository", self.root / "preparation"
        for path in (self.repository, self.preparation):
            path.mkdir(mode=0o700)
        self.output = self.root / ("1" * 32)
        self.build, self.verification = self.root / "build.json", self.root / "verification.json"
        self.build.write_bytes(b"{}")
        self.verification.write_bytes(files.encoded({"archive": {"bytes": 1, "sha256": files.sha(b"x")}}))
        for path in (self.build, self.verification):
            path.chmod(0o600)
        self.tick = 100.0
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(package.time, "monotonic", side_effect=lambda: self.tick))
        self.stack.enter_context(mock.patch.object(package, "helpers", return_value=(
            adapter, files, receiver, materializer, runner)))
        self.stack.enter_context(mock.patch.object(package, "code_identity", return_value={
            "producer": "2" * 40, "script_sha256": "3" * 64}))
        self.stack.enter_context(mock.patch("subprocess.Popen", side_effect=AssertionError("no process in unit tests")))
        self.stack.enter_context(mock.patch("socket.socket", side_effect=AssertionError("no network")))

    def call(self, **changes):
        options = {"preparation_sha256": "4" * 64, "build_sha256": files.sha(self.build.read_bytes()),
            "verification_sha256": files.sha(self.verification.read_bytes()), "deadline": 120.0}
        options.update(changes)
        return package.package(self.repository, self.preparation, self.build, self.verification,
                               self.output, **options)

    def component(self, write, role="build"):
        if not self.output.exists():
            self.output.mkdir(mode=0o700)
        fd = files.directory(self.output, private=True)
        try:
            return package.component(self.output, fd, role, write, 120.0, adapter, files, receiver)
        finally:
            os.close(fd)

    def tree(self, data):
        raw = package.inventory({name: ("100644", runner.blob_id(value), len(value))
                                 for name, value in data.items()}, materializer)
        return raw, materializer.expected_tree(raw)

    def archive(self, data):
        _, tree = self.tree(data)
        stream = io.BytesIO()
        output = package.BoundedOutput(stream, adapter.CAPS["preparation"], 120.0, adapter)
        package.tar_into(output, tree, lambda name, *_: data[name], adapter, runner, 120.0)
        return stream.getvalue()

    def test_inventory_and_tar_are_deterministic_and_preserve_empty_marker(self):
        data = {"z": b"z", "context/service/checker/packages/empty": b"", "a": b"a"}
        first = self.archive(data)
        self.assertEqual(first, self.archive(dict(reversed(list(data.items())))))
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:") as archive:
            self.assertEqual(archive.getnames(), sorted(data))
            for member in archive:
                self.assertTrue(member.isfile())
                self.assertEqual((member.uid, member.gid, member.mtime, member.uname, member.gname),
                                 (0, 0, 0, "", ""))
                self.assertEqual(archive.extractfile(member).read(), data[member.name])

    def test_tar_preserves_existing_long_path_and_materializer_contract(self):
        name = "/".join(["p" * 60] * 5) + "/empty"
        data = {name: b"", "script": b"synthetic"}
        _, tree = self.tree(data)
        self.output.mkdir(mode=0o700)
        for path in tree:
            with adapter.private_parents(self.output, path, files, 120.0):
                pass
        materializer.materialize(self.output, self.archive(data), tree, 120.0)
        self.assertEqual((self.output / name).read_bytes(), b"")
        self.assertEqual((self.output / "script").read_bytes(), b"synthetic")

    def test_inventory_rejects_unsafe_names_modes_sizes_counts_and_case_collisions(self):
        row = ("100644", "0" * 40, 1)
        cases = [{name: row} for name in ("../escape", "/absolute", "a//b", ".git/config", "a/./b")]
        cases += [{"A": row, "a": row}, {"p/A": row, "P/b": row},
                  {"link": ("120000", "0" * 40, 1)},
                  {"large": ("100644", "0" * 40, (16 << 20) + 1)},
                  {str(n): ("100644", "0" * 40, 16 << 20) for n in range(5)},
                  {str(n): row for n in range(4097)}]
        for rows in cases:
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                package.inventory(rows, materializer)

    def test_tar_rejects_wrong_blob_bytes_before_member_write(self):
        _, tree = self.tree({"safe": b"right"})
        output = package.BoundedOutput(io.BytesIO(), 80 << 20, 120.0, adapter)
        with self.assertRaises(ValueError):
            package.tar_into(output, tree, lambda *_: b"wrong", adapter, runner, 120.0)

    def test_source_executable_mode_is_preserved_without_host_metadata(self):
        raw = b"synthetic executable bytes"
        tree = {"tool": ("100755", runner.blob_id(raw), len(raw))}
        stream = io.BytesIO()
        package.tar_into(package.BoundedOutput(stream, 80 << 20, 120.0, adapter),
                         tree, lambda *_: raw, adapter, runner, 120.0)
        with tarfile.open(fileobj=io.BytesIO(stream.getvalue()), mode="r:") as archive:
            self.assertEqual(archive.getmember("tool").mode, 0o755)

    def test_bounded_output_checks_cap_deadline_and_short_write(self):
        output = package.BoundedOutput(io.BytesIO(), 1, 120.0, adapter)
        output.write(b"x")
        with self.assertRaises(ValueError):
            output.write(b"y")
        self.tick = 121.0
        with self.assertRaises(ValueError):
            output.write(b"")
        self.tick = 100.0
        short = types.SimpleNamespace(write=lambda raw: 0)
        with self.assertRaises(ValueError):
            package.BoundedOutput(short, 10, 120.0, adapter).write(b"x")

    def test_component_is_private_reopened_and_exclusive(self):
        result = self.component(lambda stream: stream.write(b"synthetic"))
        self.assertEqual(result, {"bytes": 9, "sha256": files.sha(b"synthetic")})
        self.assertEqual((self.output / "build/payload").read_bytes(), b"synthetic")
        self.assertEqual(stat.S_IMODE((self.output / "build").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.output / "build/payload").stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            self.component(lambda stream: stream.write(b"replacement"))

    def test_failed_component_keeps_partial_and_rejects_replay(self):
        def write(stream):
            stream.write(b"partial")
            raise OSError("synthetic-private-canary")
        with self.assertRaises(OSError):
            self.component(write)
        self.assertEqual((self.output / "build/payload").read_bytes(), b"partial")
        with self.assertRaises(FileExistsError):
            self.component(lambda stream: stream.write(b"replacement"))

    def test_component_cannot_adopt_symlink_directory_or_replaced_payload(self):
        self.output.mkdir(mode=0o700)
        (self.output / "build").symlink_to(self.preparation, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            self.component(lambda stream: stream.write(b"not written"))
        self.assertFalse((self.preparation / "payload").exists())
        original = receiver.rehash
        def substituted(path, *args):
            preserved = path.with_name("preserved")
            path.rename(preserved)
            path.write_bytes(preserved.read_bytes())
            path.chmod(0o600)
            original(path, *args)
        with mock.patch.object(receiver, "rehash", side_effect=substituted), self.assertRaises(ValueError):
            self.component(lambda stream: stream.write(b"same bytes"), role="verification")
        self.assertEqual((self.output / "verification/preserved").read_bytes(), b"same bytes")
        self.assertEqual((self.output / "verification/payload").read_bytes(), b"same bytes")

    def test_rehash_and_final_component_cleanup_cannot_cross_deadline_successfully(self):
        original = receiver.rehash
        def late(*args):
            original(*args)
            self.tick = 121.0
        with mock.patch.object(receiver, "rehash", side_effect=late), self.assertRaises(ValueError):
            self.component(lambda stream: stream.write(b"retained"))
        self.assertEqual((self.output / "build/payload").read_bytes(), b"retained")

    def test_git_capture_is_offline_bounded_and_uses_unchanged_deadline(self):
        observed = types.SimpleNamespace(returncode=0, stdout=b"blob", stderr=b"")
        capture = mock.Mock(return_value=observed)
        fake = types.SimpleNamespace(execute_process_group=capture, TOOL_PATH=runner.TOOL_PATH)
        result = package.git(self.repository, ["cat-file", "blob", "a" * 40], 4,
                             120.0, adapter, materializer, fake)
        self.assertEqual(result, b"blob")
        args, kw = capture.call_args
        self.assertEqual(args[0][-3:], ["cat-file", "blob", "a" * 40])
        self.assertEqual(kw["timeout"], 20.0)
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["max_output_bytes"], 4)
        for key, value in (("GIT_ALLOW_PROTOCOL", ""), ("GIT_NO_LAZY_FETCH", "1"),
                           ("GIT_NO_REPLACE_OBJECTS", "1"), ("HOME", "/nonexistent")):
            self.assertEqual(kw["env"][key], value)
        self.assertIn("core.hooksPath=/dev/null", args[0])
        self.assertIn("maintenance.auto=false", args[0])
        self.assertIn("gc.auto=0", args[0])

    def test_git_failure_overflow_stderr_and_late_cleanup_are_rejected(self):
        for code, raw, stderr in ((1, b"", b""), (0, b"xx", b""), (0, b"x", b"warning")):
            fake = types.SimpleNamespace(TOOL_PATH=runner.TOOL_PATH,
                execute_process_group=mock.Mock(return_value=types.SimpleNamespace(
                    returncode=code, stdout=raw, stderr=stderr)))
            with self.subTest(code=code, raw=raw), self.assertRaises(ValueError):
                package.git(self.repository, [], 1, 120.0, adapter, materializer, fake)
        def late(*args, **kwargs):
            self.tick = 121.0
            return types.SimpleNamespace(returncode=0, stdout=b"x", stderr=b"")
        with self.assertRaises(ValueError):
            package.git(self.repository, [], 1, 120.0, adapter, materializer,
                        types.SimpleNamespace(TOOL_PATH=runner.TOOL_PATH, execute_process_group=late))

    def test_claim_precedes_payload_and_failure_preserves_original_bytes_without_replay(self):
        original = package.component
        def observed(*args):
            self.assertTrue((self.output / "intent.json").is_file())
            return original(*args)
        with mock.patch.object(package, "component", side_effect=observed), \
                mock.patch.object(package, "git", side_effect=OSError("synthetic-private-canary")), \
                self.assertRaisesRegex(ValueError, "^original-image-package-rejected$"):
            self.call()
        self.assertEqual((self.output / "build/payload").read_bytes(), self.build.read_bytes())
        self.assertEqual((self.output / "verification/payload").read_bytes(), self.verification.read_bytes())
        raw = (self.output / "failure.json").read_bytes()
        failure = json.loads(raw)
        self.assertEqual((failure["stage"], failure["result"]), ("source", "fail"))
        self.assertFalse(failure["components_verified"])
        self.assertTrue(all(failure[key] is False for key in adapter.FLAGS))
        self.assertNotIn(b"synthetic-private-canary", raw)
        self.assertNotIn(str(self.root).encode(), raw)
        with self.assertRaises(ValueError):
            self.call()
        self.assertEqual((self.output / "failure.json").read_bytes(), raw)

    def test_wrong_record_pin_preserves_claim_without_copying_unbound_record(self):
        with self.assertRaises(ValueError):
            self.call(build_sha256="0" * 64)
        self.assertTrue((self.output / "intent.json").is_file())
        self.assertFalse((self.output / "build").exists())
        self.assertEqual(json.loads((self.output / "failure.json").read_bytes())["stage"], "records")

    def test_source_tree_mismatch_is_rejected_without_source_archive(self):
        with mock.patch.object(package, "git", return_value=b"unbound tree"), self.assertRaises(ValueError):
            self.call()
        self.assertFalse((self.output / "source").exists())
        self.assertFalse((self.output / "source-tree").exists())
        self.assertTrue((self.output / "failure.json").is_file())

    def test_deadline_and_pin_types_fail_before_attempt_creation(self):
        for changes in ({"deadline": True}, {"deadline": 99}, {"deadline": 1901},
                        {"deadline": float("nan")}, {"preparation_sha256": 7}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call(**changes)
            self.assertFalse(self.output.exists())

    def test_output_cannot_mutate_input_tree(self):
        self.output = self.preparation / ("1" * 32)
        with self.assertRaises(ValueError):
            self.call()
        self.assertFalse(self.output.exists())

    def test_expired_failure_record_does_not_rearm_deadline(self):
        def expired(*args):
            self.tick = 121.0
            raise OSError("synthetic")
        with mock.patch.object(package, "git", side_effect=expired), self.assertRaises(ValueError):
            self.call()
        self.assertTrue((self.output / "intent.json").is_file())
        self.assertFalse((self.output / "failure.json").exists())
        self.assertTrue((self.output / "verification/payload").is_file())

    def test_failed_failure_journal_cannot_remove_claim_or_mask_rejection(self):
        original = files.publish
        def publish(fd, name, value):
            if name == "failure.json":
                raise OSError("synthetic journal failure")
            return original(fd, name, value)
        with mock.patch.object(files, "publish", side_effect=publish), \
                mock.patch.object(package, "git", side_effect=OSError("synthetic")), \
                self.assertRaisesRegex(ValueError, "^original-image-package-rejected$"):
            self.call()
        self.assertTrue((self.output / "intent.json").is_file())
        self.assertTrue((self.output / "build/payload").is_file())
        self.assertFalse((self.output / "receipt.json").exists())

    def test_preparation_declared_bounds_are_checked_before_validator_reads(self):
        cases = {"large": {"large": {"bytes": (16 << 20) + 1}},
                 "boolean": {"a": {"bytes": True}}, "traversal": {"../a": {"bytes": 1}},
                 "case": {"a": {"bytes": 1}, "A": {"bytes": 1}},
                 "total": {str(n): {"bytes": 16 << 20} for n in range(5)}}
        for case, rows in cases.items():
            raw = files.encoded({"files": rows})
            path = self.preparation / "receipt.json"
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.subTest(case=case), mock.patch.object(files, "preparation_helper") as validate, \
                    self.assertRaises(ValueError):
                package.preparation_files(self.preparation, files.sha(raw), files,
                                          materializer, runner, adapter, 120.0)
            validate.assert_not_called()


class FrozenValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_selected_image_archive as fixtures
        cls.fixtures = fixtures
        fixtures.HandoffTests.setUpClass()

    @contextmanager
    def fixture(self):
        with self.fixtures.HandoffTests().fixture() as args:
            root = args.archive.parent
            raw = files.strict_json((args.preparation / "receipt.json").read_bytes())
            raw["git_sha"] = adapter.IMAGE_PRODUCER
            prep_raw = files.encoded(raw)
            (args.preparation / "receipt.json").write_bytes(prep_raw)
            args.preparation_sha256 = files.sha(prep_raw)
            build = files.strict_json(args.build_report.read_bytes())
            build["preparation_sha256"] = args.preparation_sha256
            args.build_report.write_bytes(files.encoded(build))
            args.build_sha256 = files.sha(args.build_report.read_bytes())
            observed = self.fixtures.verify.verify(args)
            report = {"schema": files.SCHEMA, "result": "pass", "producer": adapter.IMAGE_PRODUCER,
                "script_sha256": adapter.PINS[adapter.VERIFIER], "helpers": files.HELPERS,
                "limits": files.LIMITS, "profile": files.PROFILE, "archive_verified": True,
                "worker_exit_confirmed": True, **observed, "started_utc": "synthetic", "ended_utc": "synthetic",
                "expected": {"archive_sha256": args.archive_sha256, "archive_bytes": args.archive_bytes,
                    "preparation_sha256": args.preparation_sha256, "build_sha256": args.build_sha256},
                **{name: False for name in ("image_loaded", "runtime_started", "deployable",
                                          "hardware_validated", "published", "confidential")}}
            args.verification = root / "verification.json"
            args.verification.write_bytes(files.encoded(report))
            args.verification.chmod(0o600)
            args.verification_sha256 = files.sha(args.verification.read_bytes())
            # This is a tiny generated OCI fixture, not an operator artifact.
            # Removing it proves the packager does not need any image path.
            args.archive.unlink()
            yield args

    def test_real_d18_objects_preparation_and_receipt_joins_without_image_access(self):
        with self.fixture() as args, mock.patch("socket.socket", side_effect=AssertionError("no network")):
            expected = {path.relative_to(args.preparation).as_posix(): path.read_bytes()
                        for path in args.preparation.rglob("*") if path.is_file()}
            output = args.preparation.parent / ("9" * 32)
            def call():
                return package.package(ROOT, args.preparation, args.build_report, args.verification, output,
                    preparation_sha256=args.preparation_sha256, build_sha256=args.build_sha256,
                    verification_sha256=args.verification_sha256, deadline=time.monotonic() + 300)
            result = call()
            self.assertTrue(result["components_verified"])
            self.assertTrue(result["inputs_joined"])
            self.assertFalse(result["image_content_verified"])
            self.assertTrue(all(result[name] is False for name in adapter.FLAGS))
            self.assertEqual(result["components"]["image"],
                             {"bytes": args.archive_bytes, "sha256": args.archive_sha256})
            self.assertEqual(result["written_components"], list(package.ROLES))
            self.assertFalse((output / "image").exists())
            self.assertFalse(args.archive.exists())
            self.assertEqual((output / "build/payload").read_bytes(), args.build_report.read_bytes())
            self.assertEqual((output / "verification/payload").read_bytes(), args.verification.read_bytes())
            self.assertEqual(files.sha((output / "source-tree/payload").read_bytes()), adapter.SOURCE_TREE_SHA)
            self.assertFalse((output / "source-files" / package.SELF).exists())
            for name, raw in expected.items():
                self.assertEqual((output / "preparation-files" / name).read_bytes(), raw)
                self.assertEqual((args.preparation / name).read_bytes(), raw)
            original = (output / "receipt.json").read_bytes()
            with self.assertRaises(ValueError):
                call()
            self.assertEqual((output / "receipt.json").read_bytes(), original)
            self.assertNotIn(str(args.preparation.parent).encode(), original)

    def test_actual_preparation_validator_rejects_extra_link_special_and_unblessed_empty(self):
        for kind in ("extra", "symlink", "fifo", "empty"):
            with self.subTest(kind=kind), self.fixture() as args:
                path = args.preparation / "unexpected"
                if kind == "symlink":
                    path.symlink_to(args.build_report)
                elif kind == "fifo":
                    os.mkfifo(path, 0o600)
                elif kind == "extra":
                    path.write_bytes(b"synthetic ignored-file canary")
                else:
                    receipt = json.loads((args.preparation / "receipt.json").read_bytes())
                    name = next(name for name in receipt["files"]
                                if not name.startswith("context/service/checker/packages/"))
                    (args.preparation / name).write_bytes(b"")
                    receipt["files"][name] = {"bytes": 0, "sha256": files.sha(b"")}
                    (args.preparation / "receipt.json").write_bytes(files.encoded(receipt))
                    args.preparation_sha256 = files.sha(files.encoded(receipt))
                with self.assertRaises((ValueError, OSError)):
                    package.preparation_files(args.preparation, args.preparation_sha256,
                        files, materializer, runner, adapter, time.monotonic() + 30)


if __name__ == "__main__":
    unittest.main()
