# SPDX-License-Identifier: Apache-2.0
"""Software-only fixtures: local transport processes, never SSH/network/Docker."""

import copy
from contextlib import ExitStack, contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import types
import unittest
from unittest import mock

import original_image_transfer as sender

ROOT = Path(__file__).absolute().parents[1]
adapter = sender.load((ROOT / sender.ENTRY).read_bytes(), ROOT / sender.ENTRY)
files, receiver, materializer = adapter.helpers()


def encoded(value):
    return files.encoded(value)


def tree_and_tar(rows):
    tree, output = b"", io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, raw in rows.items():
            oid = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            tree += f"100644 blob {oid} {len(raw)}\t{name}\0".encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return tree, output.getvalue()


def synthetic_exception_chain(error):
    """Test diagnostics only: no exception messages, payloads, argv or full paths."""
    rows, seen = [], set()
    while error is not None and id(error) not in seen and len(rows) < 8:
        seen.add(id(error))
        frames, trace = [], error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            frames.append({"file": Path(code.co_filename).name, "function": code.co_name,
                           "line": trace.tb_lineno})
            trace = trace.tb_next
        rows.append({"exception": type(error).__name__, "errno": getattr(error, "errno", None),
                     "frames": frames[-16:]})
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return rows


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.parent.chmod(0o700)
        self.root = self.parent / ("1" * 32)
        self.tick = 100.0
        self.binding = {"boot_id": "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12,
                        "clock_namespace": [1, 2], "monotonic": 100.0}
        self.sources = {name: (ROOT / name).read_bytes() for name in
                        sorted((*adapter.PINS, adapter.SELF, adapter.SENDER))}
        self.value = {"schema": adapter.SCHEMA, "attempt_id": self.root.name, "producer": "2" * 40,
            "image_producer": adapter.IMAGE_PRODUCER, "seconds": 30, "destination": str(self.root),
            "code": {name: adapter.sha(raw) for name, raw in self.sources.items()},
            "components": {name: {"bytes": 1, "sha256": adapter.sha(b"x")} for name in adapter.ROLES},
            "preparation_sha256": "3" * 64}
        self.value["components"]["source-tree"]["sha256"] = adapter.SOURCE_TREE_SHA
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(adapter.time, "monotonic", side_effect=lambda: self.tick))
        self.stack.enter_context(mock.patch.object(adapter, "clock", side_effect=lambda: {
            **self.binding, "monotonic": self.tick}))
        self.stack.enter_context(mock.patch("subprocess.Popen", side_effect=AssertionError("no process execution")))
        self.stack.enter_context(mock.patch("socket.socket", side_effect=AssertionError("no network")))

    def packet(self):
        raw = encoded(self.value)
        return {"root": str(self.root), "manifest": raw.hex(), "manifest_sha256": adapter.sha(raw),
                "clock": self.binding, "deadline": 120.0}

    def initialize(self):
        result = adapter.initialize(self.packet(), self.sources)
        self.intent_sha = result["intent_sha256"]
        return result

    def prepare_phase(self, name="build"):
        self.initialize()
        previous = ("bootstrap", *adapter.ROLES)[(*adapter.ROLES, "final").index(name)]
        if previous == "bootstrap":
            raw = (self.root / "bootstrap.ack.json").read_bytes()
        else:
            result = adapter.ack(files, self.value, self.packet()["manifest_sha256"], previous,
                                 "4" * 64, 120.0, self.binding, intent_sha256=self.intent_sha)
            raw = encoded(result)
            path = self.root / (previous + ".ack.json")
            path.write_bytes(raw)
            path.chmod(0o600)
        return adapter.sha(raw)

    def phase(self, previous, payload=b"x", name="build", eof=True):
        read_fd, write_fd = os.pipe()
        try:
            if payload:
                os.write(write_fd, payload)
            if eof:
                os.close(write_fd)
                write_fd = None
            with mock.patch.object(adapter, "helpers", return_value=(files, receiver, materializer)):
                return adapter.phase(self.root, self.packet()["manifest_sha256"], name, previous,
                                     read_fd, self.binding, 120.0, self.intent_sha, self.sources[adapter.VERIFIER])
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

    def test_authentication_rejects_wrong_out_of_band_pin_before_import(self):
        with mock.patch.object(sender, "load", side_effect=AssertionError("no import before authentication")):
            with self.assertRaises(ValueError):
                sender.authenticated(encoded(self.value), "0" * 64)

    def test_synthetic_diagnostics_keep_hidden_errno_without_exception_contents(self):
        try:
            try:
                raise PermissionError(13, "synthetic-private-canary", "/synthetic-private-canary")
            except OSError:
                raise ValueError("original-image-transfer-rejected") from None
        except ValueError as error:
            rows = synthetic_exception_chain(error)
        self.assertEqual([row["exception"] for row in rows], ["ValueError", "PermissionError"])
        self.assertEqual(rows[1]["errno"], 13)
        self.assertTrue(rows[1]["frames"])
        self.assertNotIn(b"synthetic-private-canary", encoded(rows))

    def test_authenticated_complete_code_graph_and_distinct_producers(self):
        value, sources, _, _, _ = sender.authenticated(encoded(self.value), adapter.sha(encoded(self.value)))
        self.assertEqual(sources, self.sources)
        self.assertNotEqual(value["producer"], value["image_producer"])

    def test_wrong_adapter_pin_rejected_before_adapter_execution(self):
        self.value["code"][adapter.SELF] = "0" * 64
        original = sender.load
        def guarded(raw, path):
            self.assertNotEqual(str(path), str(ROOT / adapter.SELF))
            return original(raw, path)
        with mock.patch.object(sender, "load", side_effect=guarded), self.assertRaises(ValueError):
            sender.authenticated(encoded(self.value), adapter.sha(encoded(self.value)))

    def test_manifest_closed_roles_source_pin_types_and_limits(self):
        mutations = [lambda v: v.update(extra=True), lambda v: v.update(seconds=True),
            lambda v: v.update(seconds=1801), lambda v: v.update(producer=adapter.IMAGE_PRODUCER),
            lambda v: v.update(image_producer="0" * 40),
            lambda v: v["components"].pop("build"),
            lambda v: v["components"].update(extra={"bytes": 1, "sha256": "a" * 64}),
            lambda v: v["components"]["source-tree"].update(sha256="0" * 64),
            lambda v: v["components"]["preparation"].update(bytes=(80 << 20) + 1),
            lambda v: v["components"]["image"].update(bytes=True),
            lambda v: v["code"].update({adapter.MATERIALIZER: "0" * 64}),
            lambda v: v.update(destination=str(self.parent / "different"))]
        for mutate in mutations:
            value = copy.deepcopy(self.value)
            mutate(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                raw = encoded(value)
                adapter.manifest(raw, adapter.sha(raw), files)

    def test_bootstrap_has_authenticated_code_not_stdin_or_unverified_import(self):
        program = sender.bootstrap_program({**self.packet(), "action": "bootstrap"},
                                           self.sources, self.value["code"])
        compile(program, "synthetic-bootstrap", "exec")
        self.assertLess(program.index("any(hashlib.sha256"), program.index("exec(compile"))
        self.assertNotIn("sys.path", program)
        with self.assertRaises(ValueError):
            sender.bootstrap_program(self.packet(), {**self.sources, adapter.SELF: b"changed"}, self.value["code"])

    def test_bootstrap_durable_claim_before_code_and_no_reuse(self):
        original = adapter.write_file
        def observed(parent, name, raw, deadline):
            self.assertTrue((self.root / "intent.json").is_file())
            return original(parent, name, raw, deadline)
        with mock.patch.object(adapter, "write_file", side_effect=observed):
            result = self.initialize()
        self.assertTrue(all(result[key] is False for key in adapter.FLAGS))
        with self.assertRaises(FileExistsError):
            self.initialize()
        self.assertEqual((self.root / adapter.SELF).read_bytes(), self.sources[adapter.SELF])

    def test_failed_bootstrap_preserves_claim_and_prevents_reinitialization(self):
        with mock.patch.object(adapter, "write_file", side_effect=OSError("synthetic")), self.assertRaises(OSError):
            self.initialize()
        self.assertTrue((self.root / "intent.json").is_file())
        with self.assertRaises(FileExistsError):
            self.initialize()

    def test_bootstrap_canonical_code_order_creates_every_parent_private_under_umask_022(self):
        canonical = json.loads(encoded(self.value))["code"]
        self.assertEqual(list(self.sources), list(canonical))
        self.assertLess(list(canonical).index(adapter.RUNNER), list(canonical).index(adapter.RECEIVER))
        previous_umask = os.umask(0o022)
        try:
            result = self.initialize()
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(previous_umask)
        self.assertEqual(result["phase"], "bootstrap")
        for name, raw in self.sources.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        for path in (self.root, self.root / "scripts", self.root / "scripts/e2e", self.root / "host"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_private_parents_reject_existing_broad_permissions_and_symlinks(self):
        self.root.mkdir(mode=0o700)
        broad = self.root / "broad"
        broad.mkdir(mode=0o700)
        broad.chmod(0o755)
        (self.root / "alias").symlink_to(broad)
        for name in ("broad", "alias"):
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                with adapter.private_parents(self.root, name + "/nested/file", files, 120.0):
                    self.fail("unowned parent accepted")
        self.assertEqual(stat.S_IMODE(broad.stat().st_mode), 0o755)
        self.assertFalse((broad / "nested").exists())
        self.assertTrue((self.root / "alias").is_symlink())

    def assert_bootstrap_enumeration_failure(self, relative):
        failed_path = self.root / relative
        original = os.scandir
        observed = []
        def scandir(path):
            if Path(path) == failed_path:
                observed.append(Path(path))
                raise PermissionError("synthetic enumeration failure")
            return original(path)
        with mock.patch.object(adapter.os, "scandir", side_effect=scandir), self.assertRaises(PermissionError):
            self.initialize()
        self.assertEqual(observed, [failed_path])
        claim = (self.root / "intent.json").read_bytes()
        self.assertTrue(claim)
        self.assertEqual((self.root / "manifest.json").read_bytes(), encoded(self.value))
        self.assertFalse((self.root / "bootstrap.ack.json").exists())
        for name, raw in self.sources.items():
            self.assertEqual((self.root / name).read_bytes(), raw)
        with self.assertRaises(FileExistsError):
            self.initialize()
        self.assertEqual((self.root / "intent.json").read_bytes(), claim)

    def test_bootstrap_root_enumeration_failure_preserves_claim_without_ack(self):
        self.assert_bootstrap_enumeration_failure("")

    def test_bootstrap_nested_enumeration_failure_preserves_claim_without_ack(self):
        self.assert_bootstrap_enumeration_failure("scripts/e2e")

    def test_component_success_joins_role_request_deadline_and_preserves_bytes(self):
        previous = self.prepare_phase()
        result = self.phase(previous)
        self.assertEqual(result["request_sha256"], adapter.sha(adapter.component_request(self.value, "build", files)))
        self.assertEqual(result["deadline_monotonic"], 120.0)
        self.assertEqual(result["previous_ack_sha256"], previous)
        self.assertEqual((self.root / "build/payload").read_bytes(), b"x")
        self.assertTrue(all(result[key] is False for key in adapter.FLAGS))
        with self.assertRaises(FileExistsError):
            self.phase(previous)

    def test_incomplete_component_and_missing_ack_are_not_replayed(self):
        previous = self.prepare_phase()
        with self.assertRaises(ValueError):
            self.phase(previous, b"")
        self.assertTrue((self.root / "build.intent.json").is_file())
        self.assertEqual((self.root / "build/payload").read_bytes(), b"")
        self.assertFalse((self.root / "build.ack.json").exists())
        with self.assertRaises(FileExistsError):
            self.phase(previous)

    def test_journal_failure_after_component_keeps_partial_and_claim(self):
        previous = self.prepare_phase()
        original = files.publish
        def publish(fd, name, value):
            if name == "build.ack.json":
                raise OSError("synthetic")
            return original(fd, name, value)
        with mock.patch.object(files, "publish", side_effect=publish), self.assertRaises(OSError):
            self.phase(previous)
        self.assertEqual((self.root / "build/payload").read_bytes(), b"x")
        self.assertTrue((self.root / "build/receipt.json").is_file())
        with self.assertRaises(FileExistsError):
            self.phase(previous)

    def test_boot_clock_and_deadline_substitution_precede_payload_mutation(self):
        previous = self.prepare_phase()
        path = self.root / "intent.json"
        saved = path.read_bytes()
        for key, value in (("deadline", 125.0), ("clock", {**self.binding, "clock_namespace": [3, 4]})):
            row = json.loads(saved)
            row[key] = value
            path.write_bytes(encoded(row))
            with self.assertRaises(ValueError):
                self.phase(previous)
            self.assertFalse((self.root / "build.intent.json").exists())
        path.write_bytes(saved)
        with mock.patch.object(adapter, "clock", return_value={**self.binding, "boot_id": "a" * 36}):
            with self.assertRaises(ValueError):
                self.phase(previous)

    def test_replaced_same_content_root_is_not_adopted(self):
        previous = self.prepare_phase()
        state = json.loads((self.root / "intent.json").read_bytes())
        state["root_identity"][1] += 1
        (self.root / "intent.json").write_bytes(encoded(state))
        with self.assertRaises(ValueError):
            self.phase(previous)
        self.assertFalse((self.root / "build.intent.json").exists())

    def test_deadline_crossing_final_root_close_rejects_despite_ack_record(self):
        previous = self.prepare_phase()
        root_inode = self.root.stat().st_ino
        original = os.close
        closed = []
        def close(fd):
            info = os.fstat(fd)
            original(fd)
            closed.append(fd)
            if info.st_ino == root_inode and (self.root / "build.ack.json").exists():
                self.tick = 121.0
        with mock.patch.object(adapter.os, "close", side_effect=close), self.assertRaises(ValueError):
            self.phase(previous)
        self.assertTrue((self.root / "build.ack.json").exists())
        self.assertTrue((self.root / "build/receipt.json").exists())
        self.assertEqual((self.root / "build/payload").read_bytes(), b"x")
        for fd in set(closed):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_wrong_previous_ack_never_begins_component(self):
        self.prepare_phase()
        with self.assertRaises(ValueError):
            self.phase("0" * 64)
        self.assertFalse((self.root / "build.intent.json").exists())

    def component_chain(self):
        self.root.mkdir(mode=0o700)
        manifest_sha, intent_sha = "a" * 64, "b" * 64
        initial = adapter.ack(files, self.value, manifest_sha, "bootstrap", None, 120.0, self.binding,
                              intent_sha256=intent_sha)
        (self.root / "bootstrap.ack.json").write_bytes(encoded(initial))
        (self.root / "bootstrap.ack.json").chmod(0o600)
        previous = adapter.sha(encoded(initial))
        for role in adapter.ROLES:
            self.value["components"][role] = {"bytes": 1, "sha256": adapter.sha(b"x")}
            request = adapter.component_request(self.value, role, files)
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, b"x")
                os.close(write_fd)
                write_fd = None
                report = receiver.receive(read_fd, self.root / role, request,
                                          adapter.sha(request), deadline=120.0)
            finally:
                os.close(read_fd)
                if write_fd is not None:
                    os.close(write_fd)
            result = adapter.ack(files, self.value, manifest_sha, role, previous, 120.0, self.binding,
                intent_sha256=intent_sha, component_receipt_sha256=adapter.sha(encoded(report)))
            path = self.root / (role + ".ack.json")
            path.write_bytes(encoded(result))
            path.chmod(0o600)
            previous = adapter.sha(encoded(result))
        return manifest_sha, previous, intent_sha

    def test_final_chain_binds_every_component_receipt_and_payload_identity(self):
        manifest_sha, previous, intent_sha = self.component_chain()
        adapter.validate_components(self.root, self.value, files, 120.0, manifest_sha, previous, intent_sha)
        original = self.root / "image/payload"
        original.rename(self.root / "image/preserved-original")
        original.write_bytes(b"x")
        original.chmod(0o600)
        with self.assertRaises(ValueError):
            adapter.validate_components(self.root, self.value, files, 120.0, manifest_sha, previous, intent_sha)

    def test_final_chain_rejects_same_component_receipt_at_another_role(self):
        manifest_sha, previous, intent_sha = self.component_chain()
        path = self.root / "preparation.ack.json"
        row = json.loads(path.read_bytes())
        row["phase"] = "source"
        path.write_bytes(encoded(row))
        with self.assertRaises(ValueError):
            adapter.validate_components(self.root, self.value, files, 120.0, manifest_sha, previous, intent_sha)

    def test_materializer_preserves_empty_marker_and_separate_envelope_limits(self):
        self.root.mkdir(mode=0o700)
        rows = {"receipt.json": b"{}", "context/service/checker/packages/empty": b""}
        tree, archive = tree_and_tar(rows)
        for role, raw in (("preparation-tree", tree), ("preparation", archive)):
            (self.root / role).mkdir(mode=0o700)
            target = self.root / role / "payload"
            target.write_bytes(raw)
            target.chmod(0o600)
            self.value["components"][role] = {"bytes": len(raw), "sha256": adapter.sha(raw)}
        previous_umask = os.umask(0o022)
        try:
            target = adapter.materialize(self.root, "preparation", self.value, files, materializer, 120.0)
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(previous_umask)
        for name, raw in rows.items():
            self.assertEqual((target / name).read_bytes(), raw)
            self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o600)
        for path in target.rglob("*"):
            if path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(materializer.MAX_TREE, 64 << 20)
        self.assertEqual(materializer.MAX_ARCHIVE, 80 << 20)
        with self.assertRaises(FileExistsError):
            adapter.materialize(self.root, "preparation", self.value, files, materializer, 120.0)

    def test_materializer_failure_leaves_umask_unchanged_and_preserves_nested_artifacts(self):
        self.root.mkdir(mode=0o700)
        tree, archive = tree_and_tar({"nested/parent/file": b"synthetic retained partial"})
        for role, raw in (("preparation-tree", tree), ("preparation", archive)):
            (self.root / role).mkdir(mode=0o700)
            path = self.root / role / "payload"
            path.write_bytes(raw)
            path.chmod(0o600)
            self.value["components"][role] = {"bytes": len(raw), "sha256": adapter.sha(raw)}
        original = materializer.materialize
        def failed(*args):
            original(*args)
            raise OSError("synthetic materializer failure")
        previous_umask = os.umask(0o022)
        try:
            with mock.patch.object(materializer, "materialize", side_effect=failed), self.assertRaises(OSError):
                adapter.materialize(self.root, "preparation", self.value, files, materializer, 120.0)
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(previous_umask)
        target = self.root / "preparation-files"
        self.assertEqual((target / "nested/parent/file").read_bytes(), b"synthetic retained partial")
        for path in (target, target / "nested", target / "nested/parent"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        with self.assertRaises(FileExistsError):
            adapter.materialize(self.root, "preparation", self.value, files, materializer, 120.0)

    def test_materializer_deadline_failure_leaves_umask_unchanged_without_reset(self):
        self.root.mkdir(mode=0o700)
        tree, archive = tree_and_tar({"nested/parent/file": b"x"})
        for role, raw in (("preparation-tree", tree), ("preparation", archive)):
            (self.root / role).mkdir(mode=0o700)
            path = self.root / role / "payload"
            path.write_bytes(raw)
            path.chmod(0o600)
            self.value["components"][role] = {"bytes": len(raw), "sha256": adapter.sha(raw)}
        original = materializer.materialize
        def late(*args):
            self.assertEqual(args[-1], 120.0)
            original(*args)
            self.tick = 121.0
        previous_umask = os.umask(0o022)
        try:
            with mock.patch.object(materializer, "materialize", side_effect=late), self.assertRaises(ValueError):
                adapter.materialize(self.root, "preparation", self.value, files, materializer, 120.0)
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(previous_umask)
        self.assertEqual((self.root / "preparation-files/nested/parent/file").read_bytes(), b"x")

    def test_materializer_root_and_nested_enumeration_failure_prevent_final_ack(self):
        tree, archive = tree_and_tar({"nested/parent/file": b"synthetic retained file"})
        for relative, attempt in (("", "7" * 32), ("nested/parent", "8" * 32)):
            with self.subTest(relative=relative):
                self.root = self.parent / attempt
                self.value.update(destination=str(self.root), attempt_id=attempt)
                for role, raw in (("preparation-tree", tree), ("preparation", archive)):
                    self.value["components"][role] = {"bytes": len(raw), "sha256": adapter.sha(raw)}
                previous = self.prepare_phase("final")
                claim = (self.root / "intent.json").read_bytes()
                for role, raw in (("preparation-tree", tree), ("preparation", archive)):
                    (self.root / role).mkdir(mode=0o700)
                    (self.root / role / "payload").write_bytes(raw)
                    (self.root / role / "payload").chmod(0o600)
                failed_path = self.root / "preparation-files" / relative
                original = os.scandir
                observed = []
                def scandir(path):
                    if Path(path) == failed_path:
                        observed.append(Path(path))
                        raise PermissionError("synthetic enumeration failure")
                    return original(path)
                def join(root, value, helpers, unpacker, deadline):
                    return adapter.materialize(root, "preparation", value, helpers, unpacker, deadline)
                # Isolate the final phase's materialization durability boundary;
                # the subprocess roundtrip below exercises the complete join.
                with mock.patch.object(adapter, "validate_components"), \
                        mock.patch.object(adapter, "joined_inputs", side_effect=join), \
                        mock.patch.object(adapter.os, "scandir", side_effect=scandir), \
                        self.assertRaises(PermissionError):
                    self.phase(previous, b"", name="final")
                self.assertEqual(observed, [failed_path])
                self.assertFalse((self.root / "final.ack.json").exists())
                self.assertTrue((self.root / "final.intent.json").is_file())
                self.assertEqual((self.root / "intent.json").read_bytes(), claim)
                self.assertEqual((self.root / "preparation/payload").read_bytes(), archive)
                self.assertEqual((self.root / "preparation-files/nested/parent/file").read_bytes(),
                                 b"synthetic retained file")
                with self.assertRaises(FileExistsError):
                    self.phase(previous, b"", name="final")

    def test_materializer_rejects_traversal_special_duplicate_and_missing_members(self):
        for case in ("traversal", "symlink", "duplicate", "missing"):
            with self.subTest(case=case):
                target = self.parent / case
                target.mkdir(mode=0o700)
                tree, _ = tree_and_tar({"safe": b"x"})
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    for _ in range(2 if case == "duplicate" else 1):
                        member = tarfile.TarInfo("../unsafe" if case == "traversal" else "safe")
                        member.size = 1
                        if case == "symlink":
                            member.type, member.linkname = tarfile.SYMTYPE, "other"
                        if case != "missing":
                            archive.addfile(member, io.BytesIO(b"x"))
                with self.assertRaises(ValueError):
                    materializer.materialize(target, stream.getvalue(), materializer.expected_tree(tree), 120.0)

    def test_materializer_rejects_oversize_and_case_collisions_before_extracting(self):
        for raw in (b"100644 blob " + b"0" * 40 + b" 16777217\tlarge\0",
                    b"100644 blob " + b"0" * 40 + b" 1\ta\0" +
                    b"100644 blob " + b"0" * 40 + b" 1\tA\0"):
            with self.assertRaises(ValueError):
                materializer.expected_tree(raw)

    def test_ack_rejects_wrong_attempt_clock_deadline_flags_roles_and_extra_fields(self):
        result = adapter.ack(files, self.value, self.packet()["manifest_sha256"], "bootstrap", None,
                             120.0, self.binding, intent_sha256="a" * 64)
        for key, value in (("attempt_id", "0" * 32), ("phase", "final"),
                           ("deadline_monotonic", 125.0), ("imported", True), ("extra", 0),
                           ("clock", {**self.binding, "clock_namespace": [3, 4]})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                sender.validate_ack({**result, key: value}, self.value, self.packet()["manifest_sha256"],
                                    "bootstrap", None, self.binding, 120.0, adapter)

    def test_terminal_ack_backpressure_obeys_deadline_and_restores_descriptor(self):
        result = adapter.ack(files, self.value, self.packet()["manifest_sha256"], "bootstrap", None,
                             120.0, self.binding, intent_sha256="a" * 64)
        selector = mock.MagicMock()
        def stalled(_):
            self.tick = 121.0
            return []
        selector.__enter__.return_value.select.side_effect = stalled
        with mock.patch.object(adapter.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(os, "get_blocking", return_value=True), \
                mock.patch.object(os, "set_blocking") as blocking, \
                mock.patch.object(os, "write") as write, self.assertRaises(ValueError):
            adapter.emit(result, files)
        write.assert_not_called()
        self.assertEqual(blocking.call_args_list, [mock.call(1, False), mock.call(1, True)])

    def test_terminal_ack_short_writes_are_complete_before_success(self):
        result = adapter.ack(files, self.value, self.packet()["manifest_sha256"], "bootstrap", None,
                             120.0, self.binding, intent_sha256="a" * 64)
        selector, written = mock.MagicMock(), bytearray()
        selector.__enter__.return_value.select.return_value = [True]
        def write(fd, raw):
            self.assertEqual(fd, 1)
            written.extend(raw[:17])
            return min(17, len(raw))
        with mock.patch.object(adapter.selectors, "DefaultSelector", return_value=selector), \
                mock.patch.object(os, "get_blocking", return_value=True), \
                mock.patch.object(os, "set_blocking"), mock.patch.object(os, "write", side_effect=write):
            adapter.emit(result, files)
        self.assertEqual(bytes(written), encoded(result))

    def test_transport_requires_independent_pin_explicit_command_and_absolute_paths(self):
        value = {"schema": "nib.pinned-posix-command-transport/v1", "argv": ["/synthetic/transport", "{command}"],
                 "executable_sha256": "1" * 64, "remote_python": "/synthetic/python"}
        raw = encoded(value)
        self.assertEqual(sender.transport(raw, adapter.sha(raw), files), value)
        with self.assertRaises(ValueError):
            sender.transport(raw, "0" * 64, files)
        for change in ({"argv": ["ssh", "{command}"]}, {"argv": ["/synthetic/transport"]},
                       {"remote_python": "python3"}, {"env": {}}):
            raw = encoded({**value, **change})
            with self.assertRaises(ValueError):
                sender.transport(raw, adapter.sha(raw), files)

    def test_invoke_requires_exit_zero_and_exact_ack_no_raw_stderr(self):
        binary = self.parent / "transport"
        binary.write_bytes(b"synthetic inert binary")
        binary.chmod(0o700)
        channel = {"argv": [str(binary), "{command}"], "remote_python": "/synthetic/python",
                   "executable_sha256": adapter.sha(binary.read_bytes())}
        for code, stdout, stderr in ((1, b"{}\n", b""), (0, b"{}\n", b"synthetic"),
                                     (0, b"{}\n{}\n", b""), (0, b"{}", b"")):
            runner = types.SimpleNamespace(execute_process_group=mock.Mock(return_value=types.SimpleNamespace(
                returncode=code, stdout=stdout, stderr=stderr)))
            with self.subTest(code=code, stdout=stdout, stderr=stderr), self.assertRaises(ValueError):
                sender.invoke(channel, "pass", None, 120.0, adapter, files, runner)

    def test_deadline_is_checked_after_transport_cleanup(self):
        binary = self.parent / "transport"
        binary.write_bytes(b"inert")
        binary.chmod(0o700)
        channel = {"argv": [str(binary), "{command}"], "remote_python": "/synthetic/python",
                   "executable_sha256": adapter.sha(b"inert")}
        def finished(*args, **kwargs):
            self.tick = 121.0
            return types.SimpleNamespace(returncode=0, stdout=b"{}\n", stderr=b"")
        with self.assertRaises(ValueError):
            sender.invoke(channel, "pass", None, 120.0, adapter, files,
                          types.SimpleNamespace(execute_process_group=finished))

    def synthetic_sender(self, *, fail=None):
        # Authentication is tested separately. This boundary fake deliberately
        # uses tiny component bytes rather than packaging the real d18 tree.
        source = {"build_producer": adapter.IMAGE_PRODUCER}
        image_id = "sha256:" + "a" * 64
        raw = {name: b"synthetic " + name.encode() for name in adapter.ROLES}
        raw.update(build=encoded({"image_id": image_id}), verification=encoded({"source": source}))
        inputs = {}
        for name, data in raw.items():
            path = self.parent / (name + ".input")
            path.write_bytes(data)
            path.chmod(0o600)
            inputs[name] = path
            self.value["components"][name] = {"bytes": len(data), "sha256": adapter.sha(data)}
        channel = {"schema": "nib.pinned-posix-command-transport/v1",
            "argv": ["/synthetic/not-executable", "{command}"], "executable_sha256": "0" * 64,
            "remote_python": "/synthetic/python"}
        transport_raw, manifest_raw = encoded(channel), encoded(self.value)
        packets, events = [], []
        original = sender.bootstrap_program
        def program(packet, *args):
            packets.append(packet)
            return original(packet, *args)
        def invoke(channel, program, stream, deadline, *_):
            self.assertTrue((self.root / "intent.json").is_file())
            self.assertEqual(deadline, 120.0)
            if program.startswith("import json,os,pathlib,time"):
                events.append("clock")
                return {**self.binding, "nonce": adapter.sha(manifest_raw)}
            packet = packets[-1]
            name = packet["action"]
            events.append(name)
            self.assertTrue((self.root / (name + ".intent.json")).is_file())
            if fail == name:
                raise OSError("synthetic uncertain transport")
            fields = {}
            if name in adapter.ROLES:
                self.assertEqual(stream.read(), raw[name])
                fields = {"component": name, "request_sha256": adapter.sha(
                    adapter.component_request(self.value, name, files)), "component_receipt_sha256": "e" * 64,
                    "verified_bytes": len(raw[name]), "sha256": adapter.sha(raw[name])}
            elif name == "final":
                fields = {"source": source, "image_id": image_id,
                          "archive": self.value["components"]["image"], "inputs_joined": True}
            return adapter.ack(files, self.value, adapter.sha(manifest_raw), name, packet["previous"],
                               packet["deadline"], packet["clock"], intent_sha256="d" * 64, **fields)
        self.stack.enter_context(mock.patch.object(sender, "authenticated", return_value=(
            self.value, self.sources, adapter, files, types.SimpleNamespace())))
        self.stack.enter_context(mock.patch.object(sender, "bootstrap_program", side_effect=program))
        self.stack.enter_context(mock.patch.object(sender, "invoke", side_effect=invoke))
        def call():
            return sender.send(manifest_raw, adapter.sha(manifest_raw), transport_raw,
                adapter.sha(transport_raw), inputs, self.root, deadline=120.0)
        return call, events, packets

    def test_sender_positive_all_components_and_final_ack_share_one_deadline(self):
        call, events, packets = self.synthetic_sender()
        result = call()
        self.assertEqual(events, ["clock", "bootstrap", *adapter.ROLES, "final"])
        self.assertEqual({packet["deadline"] for packet in packets}, {120.0})
        self.assertTrue(result["inputs_joined"])
        self.assertTrue(all(result[name] is False for name in adapter.FLAGS))
        self.assertTrue((self.root / "final.exit-zero-ack.json").is_file())

    def test_sender_uncertainty_preserves_claim_stops_sequence_and_rejects_restart(self):
        call, events, _ = self.synthetic_sender(fail="source")
        with self.assertRaisesRegex(ValueError, "^original-image-transfer-rejected$"):
            call()
        before = list(events)
        self.assertEqual(events[-1], "source")
        self.assertTrue((self.root / "source.intent.json").is_file())
        self.assertFalse((self.root / "source.exit-zero-ack.json").exists())
        with self.assertRaises(ValueError):
            call()
        self.assertEqual(events, before)

    def test_sender_journal_failure_never_advances_or_retries(self):
        call, events, _ = self.synthetic_sender()
        original = files.publish
        def publish(fd, name, value):
            if name == "bootstrap.exit-zero-ack.json":
                raise OSError("synthetic journal failure")
            return original(fd, name, value)
        with mock.patch.object(files, "publish", side_effect=publish), self.assertRaises(ValueError):
            call()
        self.assertEqual(events, ["clock", "bootstrap"])
        self.assertTrue((self.root / "bootstrap.intent.json").is_file())

    def test_sender_cleanup_crossing_deadline_preserves_terminal_record_but_rejects(self):
        call, _, _ = self.synthetic_sender()
        original = os.close
        def close(fd):
            original(fd)
            if (self.root / "final.exit-zero-ack.json").exists():
                self.tick = 121.0
        with mock.patch.object(adapter.os, "close", side_effect=close), self.assertRaises(ValueError):
            call()
        self.assertTrue((self.root / "final.exit-zero-ack.json").is_file())


class SourcePinTests(unittest.TestCase):
    def test_existing_helper_pins_and_real_committed_source_relationship(self):
        from test_evidence_history import git
        raw = git(ROOT, "ls-tree", "-rlz", "--full-tree", adapter.IMAGE_PRODUCER)
        self.assertEqual(adapter.sha(raw), adapter.SOURCE_TREE_SHA)
        self.assertGreater(len(materializer.expected_tree(raw)), 2000)
        for name, digest in adapter.PINS.items():
            self.assertEqual(adapter.sha((ROOT / name).read_bytes()), digest)
        self.assertEqual(adapter.PINS[adapter.RECEIVER],
                         adapter.sha(git(ROOT, "show", "3f9fed34ed7afa9e6ee31613b91da1935b462791:" + adapter.RECEIVER)))


class JoinedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_selected_image_archive as fixtures
        cls.fixtures = fixtures
        fixtures.HandoffTests.setUpClass()

    def test_real_preparation_validator_positive_and_mismatched_original_joins(self):
        with self.fixtures.HandoffTests().fixture() as args:
            root = args.archive.parent
            prep = json.loads((args.preparation / "receipt.json").read_bytes())
            prep["git_sha"] = adapter.IMAGE_PRODUCER
            raw = encoded(prep)
            (args.preparation / "receipt.json").write_bytes(raw)
            args.preparation_sha256 = adapter.sha(raw)
            build = json.loads(args.build_report.read_bytes())
            build["preparation_sha256"] = args.preparation_sha256
            args.build_report.write_bytes(encoded(build))
            args.build_sha256 = adapter.sha(encoded(build))
            verifier = self.fixtures.verify
            _, joined = verifier.source_inputs(args)
            value = {"preparation_sha256": args.preparation_sha256, "components": {
                "build": {"bytes": args.build_report.stat().st_size, "sha256": args.build_sha256},
                "image": {"bytes": args.archive_bytes, "sha256": args.archive_sha256}}}
            receipt = {"schema": verifier.SCHEMA, "result": "pass", "producer": adapter.IMAGE_PRODUCER,
                "script_sha256": adapter.PINS[adapter.VERIFIER], "helpers": verifier.HELPERS,
                "limits": verifier.LIMITS, "profile": verifier.PROFILE, "archive_verified": True,
                "worker_exit_confirmed": True, "source": joined,
                "started_utc": "synthetic", "ended_utc": "synthetic",
                "archive": {"image_id": build["image_id"], "sha256": args.archive_sha256,
                            "bytes": args.archive_bytes, "platform": "linux/amd64"},
                "expected": {"archive_sha256": args.archive_sha256, "archive_bytes": args.archive_bytes,
                    "preparation_sha256": args.preparation_sha256, "build_sha256": args.build_sha256},
                **{key: False for key in ("image_loaded", "runtime_started", "deployable", "hardware_validated",
                                         "published", "confidential")}}
            for name, data in (("build", encoded(build)), ("verification", encoded(receipt))):
                (root / name).mkdir(mode=0o700)
                (root / name / "payload").write_bytes(data)
                (root / name / "payload").chmod(0o600)
                value["components"][name] = {"bytes": len(data), "sha256": adapter.sha(data)}
            with mock.patch.object(adapter, "materialize", side_effect=lambda root, role, *args:
                                   ROOT if role == "source" else args_preparation), \
                    mock.patch("subprocess.Popen", side_effect=AssertionError("no process")), \
                    mock.patch("socket.socket", side_effect=AssertionError("no network")):
                args_preparation = args.preparation
                result = adapter.joined_inputs(root, value, files, materializer, adapter.time.monotonic() + 30)
                self.assertEqual(result["source"], joined)
                for field, changed in (("profile", "selected"), ("producer", "0" * 40),
                                       ("source", {**joined, "prepared_sha256": "0" * 64}),
                                       ("expected", {**receipt["expected"], "archive_bytes": 1})):
                    raw = encoded({**receipt, field: changed})
                    (root / "verification/payload").write_bytes(raw)
                    value["components"]["verification"] = {"bytes": len(raw), "sha256": adapter.sha(raw)}
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        adapter.joined_inputs(root, value, files, materializer, adapter.time.monotonic() + 30)


@unittest.skipUnless(sys.platform == "linux", "receiver clock contract requires Linux")
class LocalTransportRoundtripTests(unittest.TestCase):
    """Real subprocess/pipe integration, not official image-transfer evidence.

    Only committed public Git objects supply the source fixture. Preparation,
    OCI bytes and all records are generated synthetic test data in temporary
    directories, never inputs from an operator's archive/preparation directory.
    """

    @classmethod
    def setUpClass(cls):
        import test_selected_image_archive as fixtures
        from test_evidence_history import git, GIT_ENV, GIT_OPTIONS
        cls.fixtures = fixtures
        fixtures.HandoffTests.setUpClass()
        temporary = tempfile.TemporaryDirectory(prefix="software-only-input-roundtrip-")
        cls.addClassCleanup(temporary.cleanup)
        cls.source_root = Path(temporary.name).resolve()
        cls.source_root.chmod(0o700)
        cls.source_tree = git(ROOT, "ls-tree", "-rlz", "--full-tree", adapter.IMAGE_PRODUCER)
        if adapter.sha(cls.source_tree) != adapter.SOURCE_TREE_SHA:
            raise AssertionError("committed source fixture identity changed")
        cls.producer = git(ROOT, "rev-parse", "HEAD").decode().strip()
        cls.source_archive = cls.source_root / "software-only-public-source.tar"
        runner = files.load_pinned(adapter.RUNNER, adapter.PINS[adapter.RUNNER])
        # A temporary public-code fixture only, never a deployment package.
        with cls.source_archive.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            result = runner.execute_process_group(["git", *GIT_OPTIONS,
                "-c", "core.attributesFile=/dev/null", "-C", str(ROOT),
                "archive", "--format=tar", adapter.IMAGE_PRODUCER], env=GIT_ENV,
                timeout=60, stdout_file=output, max_output_bytes=adapter.CAPS["source"],
                max_stderr_bytes=4096)
        if result.returncode != 0 or result.stderr:
            raise AssertionError("public source fixture construction failed")

    @staticmethod
    def descriptor(path):
        with path.open("rb") as stream:
            return {"bytes": os.fstat(stream.fileno()).st_size,
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}

    @contextmanager
    def fixture(self, mode):
        with self.fixtures.HandoffTests().fixture() as args:
            root = args.archive.parent
            root.chmod(0o700)
            # These edits apply only to this generated fixture's receipt/build.
            prep = json.loads((args.preparation / "receipt.json").read_bytes())
            prep["git_sha"] = adapter.IMAGE_PRODUCER
            (args.preparation / "receipt.json").write_bytes(encoded(prep))
            args.preparation_sha256 = adapter.sha(encoded(prep))
            build = json.loads(args.build_report.read_bytes())
            build["preparation_sha256"] = args.preparation_sha256
            args.build_report.write_bytes(encoded(build))
            args.build_sha256 = adapter.sha(encoded(build))
            verifier = self.fixtures.verify
            verified = verifier.verify(args)
            receipt = {"schema": verifier.SCHEMA, "result": "pass", "producer": adapter.IMAGE_PRODUCER,
                "script_sha256": adapter.PINS[adapter.VERIFIER], "helpers": verifier.HELPERS,
                "limits": verifier.LIMITS, "profile": verifier.PROFILE, "archive_verified": True,
                "worker_exit_confirmed": True, "source": verified["source"], "archive": verified["archive"],
                "started_utc": "synthetic", "ended_utc": "synthetic",
                "expected": {"archive_sha256": args.archive_sha256, "archive_bytes": args.archive_bytes,
                    "preparation_sha256": args.preparation_sha256, "build_sha256": args.build_sha256},
                **{key: False for key in ("image_loaded", "runtime_started", "deployable", "hardware_validated",
                                         "published", "confidential")}}
            rows = {path.relative_to(args.preparation).as_posix(): path.read_bytes()
                    for path in sorted(args.preparation.rglob("*")) if path.is_file()}
            preparation_tree, preparation_tar = tree_and_tar(rows)
            inputs = {"source": self.source_archive, "build": args.build_report, "image": args.archive}
            for role, raw in (("source-tree", self.source_tree), ("preparation-tree", preparation_tree),
                              ("preparation", preparation_tar), ("verification", encoded(receipt))):
                path = root / ("software-only-" + role)
                path.write_bytes(raw)
                path.chmod(0o600)
                inputs[role] = path
            local_parent, receiver_parent = root / "sender-state", root / "receiver-state"
            for path in (local_parent, receiver_parent):
                path.mkdir(mode=0o700)
            attempt = "6" * 32
            destination, local = receiver_parent / attempt, local_parent / attempt
            sources = {name: (ROOT / name).read_bytes() for name in
                       (*adapter.PINS, adapter.SELF, adapter.SENDER)}
            manifest = {"schema": adapter.SCHEMA, "attempt_id": attempt, "producer": self.producer,
                "image_producer": adapter.IMAGE_PRODUCER, "seconds": 120, "destination": str(destination),
                "code": {name: adapter.sha(raw) for name, raw in sources.items()},
                "components": {role: self.descriptor(path) for role, path in inputs.items()},
                "preparation_sha256": args.preparation_sha256}
            python = str(Path(sys.executable).resolve())
            transport_path, transport_log = root / "local-posix-transport", root / "software-only-transport.jsonl"
            transport_path.write_text(LOCAL_TRANSPORT_FIXTURE)
            transport_path.chmod(0o600)
            self.assertFalse(transport_path.stat().st_mode & 0o111)
            # Scratch stays noexec: pin and execute the existing interpreter,
            # which reads this temporary synthetic fixture as data.
            channel = {"schema": "nib.pinned-posix-command-transport/v1",
                "argv": [python, "-I", "-S", "-B", str(transport_path), mode,
                         str(destination), str(transport_log), "{command}"],
                "executable_sha256": self.descriptor(Path(python))["sha256"], "remote_python": python}
            failures = []
            def call():
                try:
                    return sender.send(encoded(manifest), adapter.sha(encoded(manifest)),
                        encoded(channel), adapter.sha(encoded(channel)), inputs, local,
                        deadline=adapter.time.monotonic() + 90)
                except Exception as error:
                    failures[:] = synthetic_exception_chain(error)
                    raise
            def events():
                if not transport_log.exists():
                    return []
                rows = []
                for line in transport_log.read_bytes().splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        rows.append({"event": "invalid-diagnostic-record", "bytes": len(line)})
                return rows
            def diagnostic():
                phases = ("bootstrap", *adapter.ROLES, "final")
                state = {"sender_exception_chain": failures, "transport_events": events(),
                    "local_intents": [name for name in phases if (local / (name + ".intent.json")).exists()],
                    "remote_acks": [name for name in phases
                                    if (destination / (name + ".ack.json")).exists()]}
                return "synthetic local transfer diagnostic: " + encoded(state).decode().strip()
            yield types.SimpleNamespace(call=call, destination=destination, local=local, manifest=manifest,
                inputs=inputs, prep_rows=rows, source=verified["source"], transport_log=transport_log,
                events=events, diagnostic=diagnostic)

    def assert_joined(self, fixture, result):
        self.assertEqual(result["phase"], "final")
        self.assertTrue(result["inputs_joined"])
        self.assertEqual(result["source"], fixture.source)
        self.assertTrue(all(result[key] is False for key in adapter.FLAGS))
        for name, digest in fixture.manifest["code"].items():
            self.assertEqual(adapter.sha((fixture.destination / name).read_bytes()), digest)
        for role, row in fixture.manifest["components"].items():
            self.assertEqual(self.descriptor(fixture.destination / role / "payload"), row)
        source = fixture.destination / "source-files"
        expected = materializer.expected_tree(self.source_tree)
        self.assertEqual({path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()},
                         set(expected))
        for name, raw in fixture.prep_rows.items():
            self.assertEqual((fixture.destination / "preparation-files" / name).read_bytes(), raw)
        deadline = json.loads((fixture.destination / "intent.json").read_bytes())["deadline"]
        for phase in ("bootstrap", *adapter.ROLES, "final"):
            remote = json.loads((fixture.destination / (phase + ".ack.json")).read_bytes())
            self.assertEqual(remote["deadline_monotonic"], deadline)

    def test_real_launcher_helpers_receiver_materializer_and_terminal_ack(self):
        with self.fixture("success") as fixture:
            try:
                result = fixture.call()
            except Exception:
                self.fail(fixture.diagnostic())
            self.assert_joined(fixture, result)
            rows = [row for row in fixture.events() if row["event"] == "exit"]
            self.assertEqual([row["phase"] for row in rows], ["clock", "bootstrap", *adapter.ROLES, "final"])
            self.assertTrue(all(row["child_exit"] == row["reported_exit"] == 0 for row in rows))
            self.assertTrue(all(row["child_stdin_pipe"] for row in rows))
            for phase in ("bootstrap", *adapter.ROLES, "final"):
                self.assertEqual((fixture.local / (phase + ".exit-zero-ack.json")).read_bytes(),
                                 (fixture.destination / (phase + ".ack.json")).read_bytes())

    def test_real_final_ack_followed_by_nonzero_transport_exit_is_rejected(self):
        with self.fixture("nonzero-after-final-ack") as fixture:
            with self.assertRaisesRegex(ValueError, "^original-image-transfer-rejected$"):
                fixture.call()
            self.assertTrue((fixture.destination / "final.ack.json").is_file(), fixture.diagnostic())
            terminal = json.loads((fixture.destination / "final.ack.json").read_bytes())
            self.assert_joined(fixture, terminal)
            rows = fixture.events()
            self.assertTrue(rows, fixture.diagnostic())
            last = rows[-1]
            self.assertEqual(last["event"], "exit", fixture.diagnostic())
            self.assertEqual((last["phase"], last["child_exit"], last["reported_exit"]),
                             ("final", 0, 23), fixture.diagnostic())
            self.assertTrue(last["ack_emitted"])
            self.assertTrue((fixture.local / "final.intent.json").is_file())
            self.assertFalse((fixture.local / "final.exit-zero-ack.json").exists())
            before = fixture.transport_log.read_bytes()
            with self.assertRaises(ValueError):
                fixture.call()
            self.assertEqual(fixture.transport_log.read_bytes(), before)

    def test_real_bootstrap_attempt_replacement_is_not_adopted(self):
        with self.fixture("replace-after-bootstrap") as fixture:
            with self.assertRaisesRegex(ValueError, "^original-image-transfer-rejected$"):
                fixture.call()
            preserved = fixture.destination.with_name(fixture.destination.name + ".preserved")
            self.assertTrue(preserved.is_dir(), fixture.diagnostic())
            self.assertNotEqual(preserved.stat().st_ino, fixture.destination.stat().st_ino)
            self.assertEqual((preserved / "intent.json").read_bytes(),
                             (fixture.destination / "intent.json").read_bytes())
            self.assertTrue((preserved / "bootstrap.ack.json").is_file())
            self.assertTrue((fixture.local / "bootstrap.exit-zero-ack.json").is_file())
            self.assertTrue((fixture.local / "source-tree.intent.json").is_file())
            self.assertFalse((fixture.local / "source-tree.exit-zero-ack.json").exists())
            self.assertFalse((fixture.destination / "source-tree.intent.json").exists())
            self.assertFalse((fixture.destination / "source-tree").exists())
            rows = [row for row in fixture.events() if row["event"] == "exit"]
            self.assertEqual([row["phase"] for row in rows], ["clock", "bootstrap", "no-ack"])
            self.assertNotEqual(rows[-1]["child_exit"], 0)
            before = fixture.transport_log.read_bytes()
            with self.assertRaises(ValueError):
                fixture.call()
            self.assertEqual(fixture.transport_log.read_bytes(), before)

    def test_pre_ack_transport_error_reports_synthetic_errno_without_terminal_lookup(self):
        with self.fixture("raise-before-child") as fixture:
            with self.assertRaisesRegex(ValueError, "^original-image-transfer-rejected$"):
                fixture.call()
            rows = fixture.events()
            self.assertTrue(rows, fixture.diagnostic())
            self.assertEqual(rows[-1]["event"], "error", fixture.diagnostic())
            self.assertEqual((rows[-1]["stage"], rows[-1]["exception"], rows[-1]["errno"]),
                             ("child-start", "PermissionError", 13), fixture.diagnostic())
            self.assertIn('"errno":13', fixture.diagnostic())
            self.assertTrue((fixture.local / "intent.json").is_file())
            self.assertFalse((fixture.local / "bootstrap.intent.json").exists())
            self.assertFalse(fixture.destination.exists())


# This non-executable driver is read by the pinned existing Python interpreter.
# It is not an SSH implementation or an operational transport configuration.
LOCAL_TRANSPORT_FIXTURE = """import hashlib,json,os,pathlib,shutil,stat,subprocess,sys
os.umask(0o022)
mode,root,log,command=sys.argv[1:]
def record(event,**fields):
 with open(log,'ab') as output:
  os.fchmod(output.fileno(),0o600)
  output.write(json.dumps({'event':event,**fields},sort_keys=True,separators=(',',':')).encode()+b'\\n')
  output.flush()
  os.fsync(output.fileno())
stage='input-read'
try:
 record('start',stage=stage)
 data=sys.stdin.buffer.read((80<<20)+1)
 if len(data)>(80<<20): raise ValueError('synthetic input bound')
 stage='child-start'
 if mode=='raise-before-child': raise PermissionError(13,'synthetic transport error')
 child=subprocess.Popen(['/bin/sh','-c',command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 pipe=stat.S_ISFIFO(os.fstat(child.stdin.fileno()).st_mode)
 stage='child-wait'
 record('started',stage=stage,input_bytes=len(data))
 stdout,stderr=child.communicate(data,timeout=60)
 stage='ack-parse'
 ack=json.loads(stdout) if stdout else {}
 phase=ack.get('phase','clock' if 'nonce' in ack else 'no-ack')
 reported=child.returncode
 stage='fault-injection'
 if child.returncode==0 and phase=='bootstrap' and mode=='replace-after-bootstrap':
  target=pathlib.Path(root)
  preserved=target.with_name(target.name+'.preserved')
  target.rename(preserved)
  shutil.copytree(preserved,target)
 if child.returncode==0 and phase=='final' and mode=='nonzero-after-final-ack': reported=23
 record('exit',phase=phase,child_exit=child.returncode,reported_exit=reported,child_stdin_pipe=pipe,
        ack_emitted=bool(stdout),stderr_bytes=len(stderr),stderr_sha256=hashlib.sha256(stderr).hexdigest())
 stage='forward-output'
 sys.stdout.buffer.write(stdout)
 sys.stdout.buffer.flush()
 sys.stderr.buffer.write(stderr)
 sys.stderr.buffer.flush()
except Exception as error:
 record('error',stage=stage,exception=type(error).__name__,errno=getattr(error,'errno',None))
 raise SystemExit(24)
raise SystemExit(reported)
"""


if __name__ == "__main__":
    unittest.main()
