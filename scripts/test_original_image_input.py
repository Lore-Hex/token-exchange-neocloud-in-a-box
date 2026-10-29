# SPDX-License-Identifier: Apache-2.0
"""Synthetic local component tests; never invoke Docker, SSH or a remote peer."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock

import original_image_input as receiver


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.parent.chmod(0o700)
        self.destination = self.parent / "attempt"
        self.raw = b"synthetic opaque component"
        self.value = {"schema": receiver.REQUEST, "attempt_id": "1" * 32,
            "producer": "2" * 40, "image_producer": "3" * 40,
            "receiver_sha256": digest((receiver.ROOT / receiver.SELF).read_bytes()),
            "bytes": len(self.raw), "sha256": digest(self.raw), "seconds": 2}
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(self.close_pipes)
        self.files = receiver.helper()
        self.addCleanup(mock.patch.stopall)
        mock.patch("subprocess.Popen", side_effect=AssertionError("no external command")).start()

    def close_writer(self):
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    def close_pipes(self):
        self.close_writer()
        if self.read_fd is not None:
            os.close(self.read_fd)
            self.read_fd = None

    def feed(self, raw=None, *, eof=True):
        raw = self.raw if raw is None else raw
        if raw:
            self.assertEqual(os.write(self.write_fd, raw), len(raw))
        if eof:
            self.close_writer()

    def call(self, *, deadline=None, value=None, raw_request=None, request_sha=None,
             destination=None, input_fd=None):
        raw_request = encoded(self.value if value is None else value) if raw_request is None else raw_request
        return receiver.receive(self.read_fd if input_fd is None else input_fd,
            self.destination if destination is None else destination, raw_request,
            digest(raw_request) if request_sha is None else request_sha,
            deadline=time.monotonic() + 1 if deadline is None else deadline)

    def assert_rejected(self, **kwargs):
        with self.assertRaisesRegex(receiver.Rejected, "^original-image-input-rejected$"):
            self.call(**kwargs)

    def test_positive_private_durable_component_and_false_authority_flags(self):
        self.feed()
        report = self.call()
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)
        self.assertEqual(set(p.name for p in self.destination.iterdir()),
                         {"intent.json", "payload", "receipt.json"})
        for name in ("payload", "intent.json", "receipt.json"):
            self.assertEqual(stat.S_IMODE((self.destination / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o700)
        self.assertEqual(report, json.loads((self.destination / "receipt.json").read_bytes()))
        self.assertEqual(report["kind"], "component-observation")
        self.assertEqual(report["scope"], "local-opaque-component")
        self.assertEqual(report["verified_bytes"], len(self.raw))
        self.assertEqual(report["sha256"], digest(self.raw))
        self.assertTrue(report["eof_observed"] and report["reopened_verified"] and report["component_verified"])
        self.assertNotIn("result", report)
        self.assertTrue(all(report[key] is False for key in receiver.FALSE_FLAGS))
        self.assertEqual(report["request_sha256"], digest(encoded(self.value)))
        self.assertEqual(report["request"], self.value)
        self.assertEqual(report["verifier_sha256"], receiver.VERIFIER_SHA)

    def test_exact_empty_component_requires_empty_hash_and_eof(self):
        self.value.update(bytes=0, sha256=digest(b""))
        self.feed(b"")
        report = self.call()
        self.assertEqual(report["verified_bytes"], 0)
        self.assertEqual((self.destination / "payload").read_bytes(), b"")

    def test_empty_component_rejects_wrong_hash_before_reserving_path(self):
        self.value["bytes"] = 0
        self.assert_rejected()
        self.assertFalse(self.destination.exists())

    def test_empty_component_rejects_nonempty_input_without_writing_it(self):
        self.value.update(bytes=0, sha256=digest(b""))
        self.feed(b"x")
        self.assert_rejected()
        self.assertEqual((self.destination / "payload").read_bytes(), b"")
        self.assertTrue((self.destination / "intent.json").exists())

    def test_exact_request_hash_and_receiver_source_are_required(self):
        self.assert_rejected(request_sha="0" * 64)
        self.assertFalse(self.destination.exists())
        self.value["receiver_sha256"] = "0" * 64
        self.assert_rejected()
        self.assertFalse(self.destination.exists())

    def test_strict_request_fields_and_types(self):
        bad_values = {
            "bytes": [True, False, -1, 1.0, "1", None, self.files.MAX_ARCHIVE + 1],
            "seconds": [True, 0, -1, 1.0, "1", self.files.SECONDS + 1],
            "sha256": [None, "A" * 64, "0" * 63, "z" * 64],
            "attempt_id": ["1" * 31, "A" * 32, None],
            "producer": ["2" * 39, "Z" * 40, None],
            "image_producer": ["3" * 39, None],
            "schema": ["other", None],
        }
        for key, values in bad_values.items():
            for bad in values:
                with self.subTest(key=key, bad=bad):
                    self.assert_rejected(value={**self.value, key: bad})
                    self.assertFalse(self.destination.exists())
        for value in ({**self.value, "extra": 1}, {k: v for k, v in self.value.items() if k != "bytes"}):
            self.assert_rejected(value=value)

    def test_request_rejects_duplicate_noncanonical_large_and_constant_json(self):
        for raw in (b'{"bytes":1,"bytes":1}', b'{"bytes":NaN}', b"[]\n",
                    json.dumps(self.value, indent=2).encode(), b" " * (self.files.MAX_REPORT + 1)):
            with self.subTest(raw=raw[:20]):
                self.assert_rejected(raw_request=raw)
                self.assertFalse(self.destination.exists())

    def test_deadline_must_be_finite_future_and_within_authenticated_budget(self):
        for deadline in (True, None, "later", float("nan"), float("inf"), -1, time.monotonic() + 10):
            with self.subTest(deadline=deadline):
                raw = encoded(self.value)
                with self.assertRaises(receiver.Rejected):
                    receiver.receive(self.read_fd, self.destination, raw, digest(raw), deadline=deadline)
                self.assertFalse(self.destination.exists())

    def test_helper_source_pin_is_enforced(self):
        with mock.patch.object(receiver, "VERIFIER_SHA", "0" * 64):
            self.assert_rejected()
        self.assertFalse(self.destination.exists())

    def test_stalled_pipe_times_out_without_waiting_for_producer_exit(self):
        started = time.monotonic()
        self.assert_rejected(deadline=started + 0.1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(self.destination.is_dir())
        self.assertTrue((self.destination / "intent.json").is_file())
        self.assertFalse((self.destination / "receipt.json").exists())
        self.assertIsNotNone(self.write_fd)

    def test_exact_bytes_without_eof_still_time_out(self):
        self.feed(eof=False)
        self.assert_rejected(deadline=time.monotonic() + 0.1)
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_empty_stream_without_eof_still_times_out(self):
        self.value.update(bytes=0, sha256=digest(b""))
        self.assert_rejected(deadline=time.monotonic() + 0.1)
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_early_eof_preserves_partial_and_blocks_same_path_retry(self):
        self.feed(self.raw[:4])
        self.assert_rejected()
        intent = (self.destination / "intent.json").read_bytes()
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw[:4])
        self.assert_rejected()
        self.assertEqual((self.destination / "intent.json").read_bytes(), intent)
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw[:4])

    def test_excess_input_is_rejected_before_excess_bytes_are_written(self):
        self.feed(self.raw + b"extra")
        self.assert_rejected()
        self.assertLessEqual((self.destination / "payload").stat().st_size, len(self.raw))
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_wrong_content_digest_preserves_actual_bytes(self):
        self.feed(b"x" * len(self.raw))
        self.assert_rejected()
        self.assertEqual((self.destination / "payload").read_bytes(), b"x" * len(self.raw))
        self.assertTrue((self.destination / "intent.json").exists())
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_short_writes_are_completed(self):
        self.feed()
        original = os.write
        with mock.patch.object(receiver.os, "write", side_effect=lambda fd, raw: original(fd, raw[:3])):
            self.call()
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)

    def test_zero_write_is_rejected_without_retry(self):
        self.feed()
        with mock.patch.object(receiver.os, "write", return_value=0) as write:
            self.assert_rejected()
        self.assertEqual(write.call_count, 1)
        self.assertEqual((self.destination / "payload").read_bytes(), b"")

    def test_pipe_descriptor_is_preserved_and_blocking_mode_restored(self):
        self.feed()
        self.call()
        self.assertTrue(stat.S_ISFIFO(os.fstat(self.read_fd).st_mode))
        self.assertTrue(os.get_blocking(self.read_fd))

    def test_nonblocking_descriptor_mode_is_preserved_on_failure(self):
        os.set_blocking(self.read_fd, False)
        self.feed(b"partial")
        self.assert_rejected()
        self.assertFalse(os.get_blocking(self.read_fd))
        self.assertTrue(stat.S_ISFIFO(os.fstat(self.read_fd).st_mode))

    def test_nonpipe_and_write_descriptor_reject_before_destination_creation(self):
        source = self.parent / "regular"
        source.write_bytes(self.raw)
        with source.open("rb") as stream:
            self.assert_rejected(input_fd=stream.fileno())
        self.assert_rejected(input_fd=self.write_fd)
        self.assert_rejected(input_fd=True)
        self.assertFalse(self.destination.exists())

    def test_existing_directory_or_symlink_is_never_adopted(self):
        self.destination.mkdir(mode=0o700)
        sentinel = self.destination / "sentinel"
        sentinel.write_bytes(b"keep")
        self.assert_rejected()
        self.assertEqual(sentinel.read_bytes(), b"keep")
        alias = self.parent / "alias"
        alias.symlink_to(self.destination, target_is_directory=True)
        self.assert_rejected(destination=alias)
        self.assertEqual(sentinel.read_bytes(), b"keep")
        dangling = self.parent / "dangling"
        dangling.symlink_to(self.parent / "absent")
        self.assert_rejected(destination=dangling)
        self.assertTrue(dangling.is_symlink())

    def test_parent_must_be_private_real_directory(self):
        self.parent.chmod(0o755)
        self.assert_rejected()
        self.assertFalse(self.destination.exists())
        self.parent.chmod(0o700)
        alias = self.parent / "alias"
        alias.symlink_to(self.parent, target_is_directory=True)
        self.assert_rejected(destination=alias / "attempt")
        self.assertFalse(self.destination.exists())

    def test_intent_is_durable_before_any_payload_write(self):
        self.feed()
        events = []
        original_publish, original_write = self.files.publish, os.write

        def publish(fd, name, value):
            original_publish(fd, name, value)
            events.append(name)

        def write(fd, raw):
            self.assertIn("intent.json", events)
            intent = json.loads((self.destination / "intent.json").read_bytes())
            self.assertEqual(intent["request_sha256"], digest(encoded(self.value)))
            return original_write(fd, raw)

        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "publish", side_effect=publish), \
                mock.patch.object(receiver.os, "write", side_effect=write):
            self.call()
        self.assertEqual(events, ["intent.json", "receipt.json"])

    def test_intent_publication_failure_preserves_reserved_directory_without_payload(self):
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "publish", side_effect=OSError("synthetic private error")):
            self.assert_rejected()
        self.assertTrue(self.destination.is_dir())
        self.assertFalse((self.destination / "payload").exists())
        self.assert_rejected()

    def test_deadline_is_rechecked_after_intent_io_before_payload_creation(self):
        original = self.files.publish
        with mock.patch.object(receiver.time, "monotonic", return_value=100) as clock:
            def publish(fd, name, value):
                original(fd, name, value)
                clock.return_value = 102
            with mock.patch.object(receiver, "helper", return_value=self.files), \
                    mock.patch.object(self.files, "publish", side_effect=publish):
                self.assert_rejected(deadline=101)
        self.assertTrue((self.destination / "intent.json").exists())
        self.assertFalse((self.destination / "payload").exists())

    def test_read_crossing_deadline_cannot_write_payload(self):
        self.feed()
        original = os.read
        with mock.patch.object(receiver.time, "monotonic", return_value=100) as clock:
            def read(fd, count):
                raw = original(fd, count)
                clock.return_value = 102
                return raw
            with mock.patch.object(receiver.os, "read", side_effect=read):
                self.assert_rejected(deadline=101)
        self.assertEqual((self.destination / "payload").read_bytes(), b"")

    def test_payload_collision_after_intent_never_overwrites(self):
        self.feed()
        original = self.files.publish
        def publish(fd, name, value):
            original(fd, name, value)
            if name == "intent.json":
                (self.destination / "payload").write_bytes(b"foreign")
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "publish", side_effect=publish):
            self.assert_rejected()
        self.assertEqual((self.destination / "payload").read_bytes(), b"foreign")

    def test_payload_symlink_after_intent_never_follows(self):
        sentinel = self.parent / "sentinel"
        sentinel.write_bytes(b"keep")
        original = self.files.publish
        def publish(fd, name, value):
            original(fd, name, value)
            if name == "intent.json":
                (self.destination / "payload").symlink_to(sentinel)
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "publish", side_effect=publish):
            self.assert_rejected()
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_payload_fsync_failure_preserves_claim_and_bytes(self):
        self.feed()
        original = os.fsync
        def sync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size == len(self.raw):
                raise OSError("synthetic full disk")
            return original(fd)
        with mock.patch.object(receiver.os, "fsync", side_effect=sync):
            self.assert_rejected()
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)
        self.assertTrue((self.destination / "intent.json").exists())
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_reopened_same_content_different_inode_is_rejected(self):
        self.feed()
        original = self.files.regular
        @contextmanager
        def regular(path, **kwargs):
            if path == self.destination / "payload":
                path.rename(self.destination / "preserved-payload")
                path.write_bytes(self.raw)
                path.chmod(0o600)
            with original(path, **kwargs) as opened:
                yield opened
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "regular", side_effect=regular):
            self.assert_rejected()
        self.assertEqual((self.destination / "preserved-payload").read_bytes(), self.raw)
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_reopened_modified_bytes_are_rejected(self):
        self.feed()
        original = self.files.regular
        @contextmanager
        def regular(path, **kwargs):
            if path == self.destination / "payload":
                path.write_bytes(b"x" * len(self.raw))
            with original(path, **kwargs) as opened:
                yield opened
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "regular", side_effect=regular):
            self.assert_rejected()
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_directory_replacement_preserves_original_and_foreign_paths(self):
        self.feed()
        original = os.read
        moved = self.parent / "preserved-attempt"
        replaced = False
        def read(fd, count):
            nonlocal replaced
            raw = original(fd, count)
            if not replaced:
                self.destination.rename(moved)
                self.destination.mkdir(mode=0o700)
                (self.destination / "sentinel").write_bytes(b"keep")
                replaced = True
            return raw
        with mock.patch.object(receiver.os, "read", side_effect=read):
            self.assert_rejected()
        self.assertEqual((self.destination / "sentinel").read_bytes(), b"keep")
        self.assertEqual((moved / "payload").read_bytes(), self.raw)
        self.assertTrue((moved / "intent.json").exists())
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_receipt_failure_never_returns_success_or_removes_payload(self):
        self.feed()
        original = self.files.publish
        def publish(fd, name, value):
            if name == "receipt.json":
                raise OSError("synthetic receipt failure")
            original(fd, name, value)
        with mock.patch.object(receiver, "helper", return_value=self.files), \
                mock.patch.object(self.files, "publish", side_effect=publish):
            self.assert_rejected()
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)
        self.assertTrue((self.destination / "intent.json").exists())

    def test_late_receipt_remains_non_authoritative_and_call_fails(self):
        self.feed()
        original = self.files.publish
        with mock.patch.object(receiver.time, "monotonic", return_value=100) as clock:
            def publish(fd, name, value):
                original(fd, name, value)
                if name == "receipt.json":
                    clock.return_value = 102
            with mock.patch.object(receiver, "helper", return_value=self.files), \
                    mock.patch.object(self.files, "publish", side_effect=publish):
                self.assert_rejected(deadline=101)
        report = json.loads((self.destination / "receipt.json").read_bytes())
        self.assertNotIn("result", report)
        self.assertTrue(all(report[key] is False for key in receiver.FALSE_FLAGS))
        self.assert_rejected()

    def test_interrupt_preserves_claim_and_does_not_close_caller_pipe(self):
        self.feed()
        with mock.patch.object(receiver.os, "write", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.call()
        self.assertTrue((self.destination / "intent.json").exists())
        self.assertTrue((self.destination / "payload").exists())
        self.assertTrue(os.get_blocking(self.read_fd))
        self.assertFalse((self.destination / "receipt.json").exists())

    def test_close_failure_is_fixed_error_and_other_descriptors_still_close(self):
        self.feed()
        original = os.close
        failed = False
        closed_after = []
        def close(fd):
            nonlocal failed
            info = os.fstat(fd)
            original(fd)
            if failed:
                closed_after.append(fd)
            elif stat.S_ISREG(info.st_mode) and info.st_size == len(self.raw):
                failed = True
                raise OSError("synthetic close failure")
        with mock.patch.object(receiver.os, "close", side_effect=close):
            self.assert_rejected()
        self.assertTrue(failed)
        self.assertGreaterEqual(len(closed_after), 2)
        self.assertEqual((self.destination / "payload").read_bytes(), self.raw)
        self.assertTrue((self.destination / "intent.json").exists())


if __name__ == "__main__":
    unittest.main()
