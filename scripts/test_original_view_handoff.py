# SPDX-License-Identifier: Apache-2.0
"""Synthetic ownership and consumer protocol checks; no mounts, Docker or network."""

from contextlib import contextmanager, ExitStack, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import original_view_consumer as consumer
import original_view_mount as mount
import selected_service_process as process
import test_selected_service_owner as owners
import test_selected_service_process as processes
import test_selected_service_smoke as smoke_tests


def contract():
    return {"schema": consumer.SCHEMA, "attempt_id": "a" * 32, "consumer_token": "b" * 32,
        "mount_contract_sha256": "c" * 64, "prepared_sha256": "d" * 64,
        "model_profile": consumer.service.models.select("original"), "model": {"device": 50, "inode": 72},
        "hard_stop_at": "2030-01-01T00:00:00Z"}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.stage, self.inspection, self.cache, self.view = mount.helpers()
        self.profile, _ = self.view.selection(self.cache, "original")
        self.plan = {"staging_receipt": str(self.root / "receipt.json"), "staging_producer": "e" * 40,
            "staging_ownership": str(self.root), "destination_export": "destination.example.test:/public"}
        self.intent = self.stage.staging_intent(self.profile, "a" * 32, "e" * 40, mount.STAGING_SHA,
            ("source.example.test:/public", self.plan["destination_export"]), (Path("/source"), Path("/destination")))
        self.intent_sha = consumer.digest(self.stage.record_bytes(self.intent))
        self.created = self.stage.created_record(self.intent_sha, {"wrapper_device": 22, "wrapper_inode": 71})
        self.destination = {"wrapper_device": 22, "wrapper_inode": 71, "model_device": 22, "model_inode": 72,
            "inventory_sha256": "f" * 64, "created_record_sha256": consumer.digest(self.stage.record_bytes(self.created))}
        resources = {"limits": self.inspection.resource_helper(self.cache).LIMITS, "limits_read_back": True,
            "empty_verified": True, "group_removed": True, "oom_kill": 0, "memory_peak_bytes": 1024,
            "populated_before_cleanup": 0}
        self.completion = {"schema": "nib.original-weight-staging-completion/v1", "staging_intent_sha256": self.intent_sha,
            "worker_acknowledgment": self.stage.acknowledgment(self.profile, "a" * 32, self.intent_sha, self.destination),
            "resources": resources}
        self.receipt = {"schema": self.stage.SCHEMA, "mode": self.stage.MODE, "model_profile": self.profile,
            "producer": "e" * 40, "script_sha256": mount.STAGING_SHA, "attempt_id": "a" * 32,
            "inspection_helper_sha256": self.stage.INSPECTION_SHA256, "view_helper_sha256": self.stage.VIEW_SHA256,
            "cache_helper_sha256": self.inspection.CACHE_SHA256, "resource_helper_sha256": self.inspection.RESOURCE_SHA256,
            "supervisor_sha256": self.cache.PROBE_SHA256, "profile_helper_sha256": self.cache.PROFILE_SHA256,
            "publication_helper_sha256": self.cache.PUBLICATION_SHA256, "result": "pass", "failure": None,
            "publication": "exclusive-directory-copy", "cleanup": "worker-completed", "worker_exit_confirmed": True,
            "files": 83, "verified_bytes": self.profile["total_bytes"], "source_preserved": True,
            "source_written": False, "source_removed": False, "partial_removed": False,
            "host_visible_mount_created": False, "immutable": False, "runtime_ready": False, "confidential": False,
            "private_namespace_verified": True, "private_mount_cleanup_confirmed": True,
            "staging_intent_sha256": self.intent_sha,
            "completion_sha256": consumer.digest(self.stage.record_bytes(self.completion)),
            "timeout_seconds": 3600, "resources": resources,
            "started_utc": "2030-01-01T00:00:00+00:00", "ended_utc": "2030-01-01T00:01:00+00:00"}
        self.write("staging-intent.json", self.intent)
        self.write("destination-created.json", self.created)
        self.write("completion.json", self.completion)
        self.receipt_write()

    def write(self, name, value):
        path = self.root / name
        path.write_bytes(self.stage.record_bytes(value))
        path.chmod(0o600)

    def receipt_write(self):
        self.write("receipt.json", self.receipt)
        self.plan["staging_receipt_sha256"] = consumer.digest((self.root / "receipt.json").read_bytes())

    def test_full_staging_chain_retains_created_destination_identity(self):
        row = mount.staging_evidence(self.plan)
        self.assertEqual(row["destination"], self.destination)
        self.assertEqual(row["receipt_sha256"], self.plan["staging_receipt_sha256"])
        self.assertEqual(row["wrapper"], ".nib-original-view-" + "a" * 32)

    def test_failed_or_mixed_receipts_never_borrow_successful_completion(self):
        original = copy.deepcopy(self.receipt)
        for change in ({"result": "fail"}, {"failure": "cancelled"}, {"cleanup": "unproven"},
                {"worker_exit_confirmed": False}, {"files": 0}, {"verified_bytes": 0},
                {"model_profile": consumer.service.models.select("uncensored")}, {"producer": "f" * 40},
                {"resource_helper_sha256": "0" * 64}, {"worker_failure": {}},
                {"private_mount_cleanup_confirmed": 1}, {"completion_sha256": "0" * 64}):
            with self.subTest(change=change):
                self.receipt = {**original, **change}
                self.receipt_write()
                with self.assertRaises(ValueError):
                    mount.staging_evidence(self.plan)

    def test_export_or_creation_record_substitution_rejected(self):
        with self.assertRaises(ValueError):
            mount.staging_evidence({**self.plan, "destination_export": "another.example.test:/public"})
        self.write("destination-created.json", {**self.created, "wrapper_inode": 999})
        with self.assertRaises(ValueError):
            mount.staging_evidence(self.plan)

    def test_completion_cannot_smuggle_resource_failure(self):
        for key, value in (("oom_kill", 1), ("oom_kill", False), ("empty_verified", False),
                           ("group_removed", False), ("limits_read_back", 1)):
            with self.subTest(key=key, value=value):
                self.setUp()
                self.receipt["resources"][key] = value
                self.write("completion.json", self.completion)
                self.receipt["completion_sha256"] = consumer.digest((self.root / "completion.json").read_bytes())
                self.receipt_write()
                with self.assertRaises(ValueError):
                    mount.staging_evidence(self.plan)

    def test_reopen_allows_new_client_device_only_with_both_original_inodes(self):
        evidence = mount.staging_evidence(self.plan)
        wrapper = SimpleNamespace(st_dev=50, st_ino=71, st_mode=stat.S_IFDIR | 0o700, st_uid=0)
        model = SimpleNamespace(st_dev=50, st_ino=72, st_mode=stat.S_IFDIR | 0o555, st_uid=0)
        self.assertEqual(mount.reopened_identity(evidence, {"device": 50}, wrapper, model),
                         {"device": 50, "inode": 72})
        # Identical model bytes cannot authorize a substituted wrapper/model.
        for target, key, value in ((wrapper, "st_ino", 700), (model, "st_ino", 700),
                (wrapper, "st_dev", 51), (model, "st_dev", 51), (wrapper, "st_mode", stat.S_IFDIR | 0o755),
                (model, "st_mode", stat.S_IFDIR | 0o777), (model, "st_uid", 65532)):
            previous = getattr(target, key)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                setattr(target, key, value)
                mount.reopened_identity(evidence, {"device": 50}, wrapper, model)
            setattr(target, key, previous)

    def test_atomic_record_publication_is_exclusive(self):
        records = mount.Records(self.root)
        records.publish("closed.json", {"closed": True})
        before = (self.root / "closed.json").read_bytes()
        with self.assertRaises(OSError):
            records.publish("closed.json", {"closed": False})
        self.assertEqual((self.root / "closed.json").read_bytes(), before)

    def test_admission_lock_excludes_second_open_until_release(self):
        (self.root / "admission.lock").touch(mode=0o600)
        with mount.admission_lock(self.root):
            with self.assertRaises(BlockingIOError):
                with mount.admission_lock(self.root):
                    self.fail("second registration entered")
        with mount.admission_lock(self.root):
            pass


class ConsumerTests(unittest.TestCase):
    def test_closed_original_contract_and_profile(self):
        self.assertEqual(consumer.validate(contract()), contract())
        for change in ({"model_profile": consumer.service.models.select("uncensored")},
                {"extra": True}, {"attempt_id": "a" * 31}, {"prepared_sha256": "x" * 64},
                {"model": {"device": True, "inode": 72}}, {"model": {"device": 50, "inode": 0}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                consumer.validate({**contract(), **change})

    def hold(self):
        hold = object.__new__(consumer.Hold)
        hold.value, hold.sha = contract(), "f" * 64
        hold.space = {"device": 4, "inode": 88}
        hold.shutdown = mock.Mock(is_set=mock.Mock(return_value=False))
        hold.connection = mock.Mock()
        return hold

    def test_exact_release_only_after_verified_ack(self):
        hold = self.hold()
        hold.connection.recv.return_value = consumer.encoded(consumer.release(hold.sha))
        with mock.patch.object(hold, "check"), mock.patch.object(hold, "send") as send, \
                mock.patch.object(hold, "model_check") as model, \
                mock.patch.object(consumer.select, "select", return_value=([hold.connection], [], [])):
            hold.verified(time.monotonic() + 10)
        send.assert_called_once_with("verified")
        model.assert_called_once()

    def test_eof_wrong_release_or_cancel_never_passes_hold(self):
        for raw in (b"", b"{}", consumer.encoded(consumer.release("0" * 64))):
            hold = self.hold()
            hold.connection.recv.return_value = raw
            with self.subTest(raw=raw), mock.patch.object(hold, "check"), mock.patch.object(hold, "send"), \
                    mock.patch.object(consumer.select, "select", return_value=([hold.connection], [], [])), \
                    self.assertRaises(ValueError):
                hold.verified(time.monotonic() + 10)
        hold.shutdown.is_set.return_value = True
        with self.assertRaises(ValueError):
            hold.check()

    def test_any_post_release_data_or_eof_cancels(self):
        hold = self.hold()
        with mock.patch.object(consumer.select, "select", return_value=([], [], [])):
            hold.check()
        with mock.patch.object(consumer.select, "select", return_value=([hold.connection], [], [])), \
                self.assertRaises(ValueError):
            hold.check()

    def test_acknowledgment_has_no_verified_counts_before_full_hash(self):
        joined = consumer.acknowledgment(contract(), "f" * 64, "joined", {"device": 1, "inode": 2})
        verified = consumer.acknowledgment(contract(), "f" * 64, "verified", joined["mount_namespace"])
        self.assertEqual((joined["files"], joined["verified_bytes"]), (0, 0))
        self.assertEqual((verified["files"], verified["verified_bytes"]), (83, 510311045295))
        self.assertEqual((verified["uid"], verified["gid"], verified["pid"]), (65532, 65532, 1))

    def test_model_identity_namespace_and_readonly_are_rechecked(self):
        hold = self.hold()
        info = SimpleNamespace(st_dev=50, st_ino=72, st_uid=0, st_mode=stat.S_IFDIR | 0o555)
        with mock.patch.object(consumer.os, "open", return_value=51), mock.patch.object(consumer.os, "close"), \
                mock.patch.object(consumer.os, "fstat", return_value=info), \
                mock.patch.object(consumer.os, "fstatvfs", return_value=SimpleNamespace(f_flag=os.ST_RDONLY)), \
                mock.patch.object(consumer, "namespace", return_value=hold.space) as space:
            hold.model_check()
            info.st_ino += 1
            with self.assertRaises(ValueError): hold.model_check()
            info.st_ino -= 1
            space.return_value = {"device": 4, "inode": 89}
            with self.assertRaises(ValueError): hold.model_check()


class OwnerLeaseTests(owners.Fixture, unittest.TestCase):
    def original(self):
        root = self.root / "original-container"
        root.mkdir(mode=0o700)
        runtime = copy.deepcopy(self.runtime)
        runtime["command"] += ["--execution-profile", consumer.PROFILE, "--original-view-sha256", "f" * 64]
        state = mount.owner.State(root)
        state.initialize(self.contract, runtime)
        self.commands.state = state
        return mount.owner.Owner(self.commands, self.reservation, state)

    def test_registration_spans_create_and_resolved_record(self):
        selected = self.original()
        events = []
        @contextmanager
        def registration(value):
            self.assertIs(value, selected)
            self.assertNotIn("container-intent.json", value.rows())
            events.append("registered")
            yield
            self.assertIn("container-resolved.json", value.rows())
            events.append("resolved")
        with mock.patch.object(mount, "registration", registration):
            selected.create()
        self.assertEqual(events, ["registered", "resolved"])

    def test_registration_failure_prevents_docker_create(self):
        selected = self.original()
        with mock.patch.object(mount, "registration", side_effect=ValueError("closed")), self.assertRaises(ValueError):
            selected.create()
        self.assertIsNone(self.commands.container)
        self.assertNotIn("container-intent.json", selected.rows())

    def test_legacy_create_does_not_acquire_new_lease(self):
        with mock.patch.object(mount, "registration", side_effect=AssertionError("legacy changed")):
            self.owned.create()

    def test_original_release_requires_verification_proof(self):
        selected = self.original()
        with mock.patch.object(mount, "registration", side_effect=lambda _: ExitStack()):
            selected.create()
        selected.begin_original_verification()
        self.commands.set_status("running")
        with mock.patch.object(mount, "verify_release", side_effect=ValueError("missing proof")), \
                self.assertRaises(ValueError):
            selected.release_for_serving()
        self.reservation.protected()

    def test_running_original_can_be_inspected_only_under_quarantine(self):
        selected = self.original()
        with mock.patch.object(mount, "registration", side_effect=lambda _: ExitStack()):
            selected.create()
        selected.begin_original_verification()
        self.commands.set_status("running")
        selected.fresh(serving=False, verifying=True)
        with self.assertRaises(ValueError): selected.fresh(serving=False)


class ReleaseJoinTests(unittest.TestCase):
    def setUp(self):
        self.value = contract()
        self.mount_contract = {"inputs": "/private/inputs", "prepared_sha256": self.value["prepared_sha256"],
            "staging": {"model_profile": self.value["model_profile"]}, "plan": {"attempt_id": self.value["attempt_id"]},
            "consumer_token": self.value["consumer_token"], "consumer_hard_stop_at": self.value["hard_stop_at"]}
        self.mount_raw = consumer.encoded(self.mount_contract)
        self.value["mount_contract_sha256"] = consumer.digest(self.mount_raw)
        self.raw = consumer.encoded(self.value)
        self.space = {"device": 4, "inode": 88}
        self.proof = {"contract_sha256": consumer.digest(self.mount_raw), "container_id": "a" * 64, "host_pid": 91,
            "acknowledgment": consumer.acknowledgment(self.value, consumer.digest(self.raw), "verified", self.space)}
        self.selected = SimpleNamespace(state=SimpleNamespace(root=Path("/private/owner/container")),
            runtime={"synthetic": "runtime"}, contract={"synthetic": "owner"},
            rows=lambda: {"container-resolved.json": {"id": "a" * 64}},
            fresh=mock.Mock(return_value={"State": {"Pid": 91, "Running": True}}))
        self.records = {
            "admitted.json": {"contract_sha256": consumer.digest(self.mount_raw), "consumer_sha256": consumer.digest(self.raw)},
            "verified.json": {"contract_sha256": consumer.digest(self.mount_raw), "model": dict(self.value["model"])},
            "consumer-joined.json": {"contract_sha256": consumer.digest(self.mount_raw), "host_pid": 91,
                "mount_namespace": self.space},
            "registered.json": {"contract_sha256": consumer.digest(self.mount_raw),
                "runtime_sha256": consumer.digest(consumer.encoded(self.selected.runtime)),
                "owner_sha256": consumer.digest(consumer.encoded(self.selected.contract))}}

    def verify(self):
        def read(path, **kwargs):
            if path.name == "contract.json": return self.mount_raw, self.mount_contract
            if path.name == "consumer-verified.json": return b"", self.proof
            if path.name == consumer.CONTRACT: return self.raw, self.value
            if path.name in self.records: return b"", self.records[path.name]
            raise AssertionError(path)
        with mock.patch.object(mount, "read", side_effect=read), \
                mock.patch.object(consumer, "namespace", return_value=self.space):
            mount.verify_release(self.selected)

    def test_exact_contract_ack_and_live_container_join(self):
        self.verify()
        self.selected.fresh.assert_called_once_with(serving=False, verifying=True)

    def test_matching_container_id_alone_is_not_release_authority(self):
        for key, value in (("contract_sha256", "0" * 64), ("prepared_sha256", "0" * 64),
                ("manifest_sha256", "0" * 64), ("pid", True), ("files", 0), ("readonly", False),
                ("model", {"device": 50, "inode": 999}), ("phase", "joined")):
            with self.subTest(key=key):
                saved = copy.deepcopy(self.proof)
                self.proof["acknowledgment"][key] = value
                with self.assertRaises(ValueError): self.verify()
                self.proof = saved
        self.proof["contract_sha256"] = "0" * 64
        with self.assertRaises(ValueError): self.verify()

    def test_changed_live_pid_or_namespace_rejects_release(self):
        self.selected.fresh.return_value["State"]["Pid"] = 92
        with self.assertRaises(ValueError): self.verify()
        self.selected.fresh.return_value["State"]["Pid"] = 91
        self.proof["acknowledgment"]["mount_namespace"] = {"device": 4, "inode": 89}
        with self.assertRaises(ValueError): self.verify()

    def test_consistent_new_ack_cannot_replace_original_admission_model_or_registration(self):
        self.value["model"]["inode"] += 1
        self.raw = consumer.encoded(self.value)
        self.records["admitted.json"]["consumer_sha256"] = consumer.digest(self.raw)
        self.proof["acknowledgment"] = consumer.acknowledgment(self.value, consumer.digest(self.raw), "verified", self.space)
        with self.assertRaises(ValueError): self.verify()
        self.setUp()
        self.records["registered.json"]["runtime_sha256"] = "0" * 64
        with self.assertRaises(ValueError): self.verify()

    def test_every_admission_join_is_required(self):
        for name in self.records:
            with self.subTest(name=name):
                previous = self.records[name]["contract_sha256"]
                self.records[name]["contract_sha256"] = "0" * 64
                with self.assertRaises(ValueError): self.verify()
                self.records[name]["contract_sha256"] = previous


class PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        smoke_tests.SmokeTests.setUpClass()

    def test_new_consumer_module_is_bound_by_preparation_not_old_image_claims(self):
        with processes.PackagingTests().prepared_fixture() as (root, payload, previous):
            path = root / "scripts/original_view_consumer.py"
            path.write_bytes(Path(consumer.__file__).read_bytes())
            row = process.preparation_receipt(root, process.digest((payload / "lab.json").read_bytes()))
            raw = json.dumps(row).encode()
            self.assertEqual(row["files"]["scripts/original_view_consumer.py"], process.digest(path.read_bytes()))
            self.assertNotEqual(process.digest(raw), previous)
            (root / "prepared.json").write_bytes(raw)
            process.Prepared(process.digest(raw), time.monotonic() + 10)
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaises(ValueError):
                process.Prepared(process.digest(raw), time.monotonic() + 10)

    def test_opt_in_admission_rejects_missing_or_old_consumer_before_inputs(self):
        fixture = smoke_tests.SmokeTests()
        fixture.setUp()
        smoke = smoke_tests.smoke
        args = copy.copy(fixture.args)
        args.original_view_plan = "/private/plan.json"
        args.execution_profile = consumer.PROFILE
        args.lifetime_profile = consumer.service.ORDINARY_PROFILE
        args.hard_stop_at = fixture.clock.timestamp(18000)
        fixture.lab_contract["execution_profile"] = consumer.PROFILE
        files = {"context/service/" + name: (smoke.ROOT / name).read_bytes() for name in
            ("scripts/selected_service.py", "scripts/selected_service_process.py",
             "scripts/selected_service_engine.py", "scripts/original_view_consumer.py")}
        key = "context/service/scripts/original_view_consumer.py"
        for mutation in (None, "missing", "stale"):
            selected = dict(files)
            if mutation == "missing": del selected[key]
            if mutation == "stale": selected[key] = b"old image"
            with self.subTest(mutation=mutation), fixture.admission() as (inspect, _), \
                    mock.patch.object(smoke.image_builder, "validated_files", return_value=(
                        {"prepared_sha256": fixture.build["prepared_sha256"]}, selected, fixture.lab_contract)):
                if mutation is None:
                    with smoke.admitted_runtime(args, smoke.report(), lifetime_profile=args.lifetime_profile) as admitted:
                        self.assertEqual(admitted[0].execution_profile, consumer.PROFILE)
                else:
                    with self.assertRaises(ValueError), smoke.admitted_runtime(args, smoke.report(),
                            lifetime_profile=args.lifetime_profile):
                        self.fail("old image admitted")
                    inspect.assert_not_called()


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.lease = object.__new__(mount.Lease)
        self.lease.root, self.lease.sha = self.root, "a" * 64
        self.lease.row = {"namespace": {"device": 4, "inode": 55}, "consumer_token": "b" * 32,
            "image_id": "sha256:" + "c" * 64, "plan": {"target": str(self.root / "target")}}
        self.lease.closed = self.lease.admission_sealed = self.lease.closure_recorded = True
        self.lease.mount_attempted, self.lease.unmount_attempted = True, False
        self.lease.daemon, self.lease.daemon_fd = (91, 101), 11
        self.lease.commands = SimpleNamespace(recorder=object(), env={})
        self.lease.stack, self.lease.records = mock.Mock(), mock.Mock()
        self.lease.worker_states = []
        self.lease.peer, self.lease.listener, self.lease.connection = None, None, None
        self.observations = mock.Mock()
        self.observations.owned.return_value = None
        self.observations.call.return_value = b""
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(mock.patch.object(mount.network, "Commands", return_value=self.observations))
        self.patches.enter_context(mock.patch.object(mount.owner, "daemon_identity", return_value=self.lease.daemon))
        self.patches.enter_context(mock.patch.object(consumer, "namespace", return_value=self.lease.row["namespace"]))
        self.patches.enter_context(mock.patch.object(mount.select, "select",
            side_effect=lambda fds, *_: ([12], [], []) if fds == [12] else ([], [], [])))
        self.capture = self.patches.enter_context(mock.patch.object(self.lease, "capture"))
        (self.root / "mount-created.json").touch()

    def owner(self, *, started=False, resolved=True):
        (self.root / "registered.json").touch()
        rows = {"container-resolved.json": {"id": "d" * 64}} if resolved else {}
        if started: rows["start-intent.json"] = {"event": "start-intent"}
        return SimpleNamespace(rows=lambda: rows)

    def test_closed_registration_and_resolved_never_started_consumer_can_retire(self):
        self.lease.retire(self.owner(), None)
        self.capture.assert_called_once_with("unmount")
        self.observations.owned.assert_any_call("nib-selected-" + "b" * 32, "b" * 32,
                                              "sha256:" + "c" * 64, "d" * 64)

    def test_missing_close_gate_never_unmounts(self):
        for field in ("closed", "admission_sealed", "closure_recorded"):
            setattr(self.lease, field, False)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.lease.retire(None, None)
            setattr(self.lease, field, True)
        self.capture.assert_not_called()

    def test_unknown_create_or_started_without_observed_peer_keeps_mount(self):
        for started, resolved in ((False, False), (True, True)):
            with self.subTest(started=started), self.assertRaises((KeyError, ValueError)):
                self.lease.retire(self.owner(started=started, resolved=resolved), None)
        self.capture.assert_not_called()

    def test_present_container_or_foreign_container_blocks_retirement(self):
        selected = self.owner()
        self.observations.owned.return_value = {"Id": "d" * 64}
        with self.assertRaises(ValueError): self.lease.retire(selected, None)
        self.observations.owned.return_value = None
        self.observations.call.return_value = b"f" * 64
        with self.assertRaises(ValueError): self.lease.retire(selected, None)
        self.capture.assert_not_called()

    def test_worker_uncertainty_blocks_unmount(self):
        with mock.patch.object(self.lease, "workers_absent", side_effect=ValueError("still present")), \
                self.assertRaises(ValueError):
            self.lease.retire(None, None)
        self.capture.assert_not_called()

    def test_lost_created_record_never_authorizes_adoption(self):
        (self.root / "mount-created.json").unlink()
        helpers = mount.helpers()
        with mock.patch.object(mount, "helpers", return_value=helpers), \
                mock.patch.object(helpers[2], "require_unmounted", side_effect=ValueError("unknown mount")), \
                self.assertRaises(ValueError):
            self.lease.retire(None, None)
        self.capture.assert_not_called()

    def test_unmount_is_not_retried_after_uncertain_outcome(self):
        self.lease.unmount_attempted = True
        with self.assertRaises(FileNotFoundError): self.lease.retire(None, None)
        self.capture.assert_not_called()

    def test_journal_failure_closes_admission_and_connection_before_failure(self):
        self.lease.closed = False
        self.lease.closure_recorded = False
        self.lease.connection, self.lease.listener = mock.Mock(), mock.Mock()
        self.lease.records.publish.side_effect = OSError("synthetic fsync failure")
        with self.assertRaises(OSError): self.lease.close_admission()
        self.assertTrue(self.lease.closed)
        self.assertTrue(self.lease.admission_sealed)
        self.assertFalse(self.lease.closure_recorded)
        self.lease.connection.close.assert_called_once()
        self.lease.listener.close.assert_called_once()
        with self.assertRaises(ValueError): self.lease.retire(None, None)
        self.capture.assert_not_called()

    def test_observations_continue_when_journal_or_other_observation_fails(self):
        self.lease.closure_recorded = False
        with mock.patch.object(self.lease, "daemon_check", side_effect=ValueError("daemon unknown")), \
                mock.patch.object(self.lease, "containers_absent", side_effect=OSError("journal unreadable")) as containers, \
                mock.patch.object(self.lease, "descendants_absent") as descendants, \
                mock.patch.object(self.lease, "workers_absent") as workers, self.assertRaises(ValueError):
            self.lease.retire(None, None)
        containers.assert_called_once()
        descendants.assert_called_once()
        workers.assert_called_once()
        self.capture.assert_not_called()

    def test_observation_survives_repeated_retirement_failure(self):
        with mock.patch.object(self.lease, "retire", side_effect=[OSError("journal"), ValueError("unknown"), None]) as retire, \
                mock.patch.object(mount.time, "sleep") as sleep:
            self.lease.retain_until_absent(None, None)
        self.assertEqual(retire.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


class ConsumerStartupTests(unittest.TestCase):
    def test_hash_and_hold_precede_engine_launch(self):
        fixture = processes.ProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        events = []
        fixture.owner.original_view = mock.Mock()
        fixture.owner.original_view.verified.side_effect = lambda _: events.append("hold-released")
        with mock.patch.object(fixture.owner, "verify_weights", side_effect=lambda _: events.append("full-hash")), \
                mock.patch.object(fixture.owner, "spawn", side_effect=lambda *_: events.append("engine") or fixture.child()):
            fixture.owner.start("engine", fixture.contract, fixture.inputs, lambda _: None, 1900)
        self.assertEqual(events[:3], ["full-hash", "hold-released", "engine"])

    def test_hold_failure_never_launches_engine(self):
        fixture = processes.ProcessTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.owner.original_view = mock.Mock()
        fixture.owner.original_view.verified.side_effect = ValueError("wrong acknowledgment")
        with mock.patch.object(fixture.owner, "verify_weights"), mock.patch.object(fixture.owner, "spawn") as spawn, \
                self.assertRaises(ValueError):
            fixture.owner.start("engine", fixture.contract, fixture.inputs, lambda _: None, 1900)
        spawn.assert_not_called()


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 64
        self.row = {"namespace": {"device": 4, "inode": 50}, "boot_id": "synthetic-boot",
            "plan": {"target": "/private/target", "destination_export": "staging.example.test:/public",
                "verification_seconds": 120}, "target": {"device": 5, "inode": 61}}
        self.mounted = {"mount_id": 71, "device": 7, "namespace": self.row["namespace"], "boot_id": self.row["boot_id"]}
        self.cache, self.inspection, self.stage, self.records = (mock.Mock() for _ in range(4))
        self.cache.unique = dict
        self.cache.command.return_value = b'{"filesystems":[{"propagation":"private"}]}'
        self.inspection.supervisor_identity.return_value = {"boot_id": self.row["boot_id"]}
        self.stage.mount_binding.return_value = (71, 7)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(mount, "helpers", return_value=(self.stage, self.inspection, self.cache, mock.Mock())))
        self.stack.enter_context(mock.patch.object(consumer, "namespace", return_value=self.row["namespace"]))
        self.stack.enter_context(mock.patch.object(consumer, "digest", return_value=self.sha))
        self.read = self.stack.enter_context(mock.patch.object(mount, "read", side_effect=lambda path:
            (b"", self.row if path.name == "contract.json" else {"mount": self.mounted, "contract_sha256": self.sha})))
        self.stack.enter_context(mock.patch.object(mount, "Records", return_value=self.records))

    def test_mount_readback_binds_export_target_boot_and_namespace(self):
        self.assertEqual(mount.mount_readback(self.row), self.mounted)
        self.stage.mount_binding.assert_called_once_with(self.inspection, self.cache,
            "staging.example.test:/public", Path("/private/target"), True)
        self.cache.command.assert_called_once_with(["findmnt", "--json", "--id", "71", "--output", "PROPAGATION"])
        self.inspection.supervisor_identity.return_value = {"boot_id": "wrong-boot"}
        with self.assertRaises(ValueError): mount.mount_readback(self.row)

    def test_shared_propagation_or_different_namespace_rejected(self):
        self.cache.command.return_value = b'{"filesystems":[{"propagation":"shared"}]}'
        with self.assertRaises(ValueError): mount.mount_readback(self.row)
        self.cache.command.return_value = b'{"filesystems":[{"propagation":"private"}]}'
        with mock.patch.object(consumer, "namespace", return_value={"device": 4, "inode": 51}), self.assertRaises(ValueError):
            mount.mount_readback(self.row)

    def test_unmount_uses_exact_owned_binding_and_only_ordinary_helper(self):
        with redirect_stdout(io.StringIO()) as output:
            mount.worker("/private/records", self.sha, "unmount")
        self.cache.unmount_cache.assert_called_once_with("staging.example.test:/public", Path("/private/target"))
        self.records.publish.assert_called_once_with("unmounted.json", {"contract_sha256": self.sha, "ordinary_unmount": True})
        self.assertEqual(json.loads(output.getvalue())["operation"], "unmount")
        self.stage.mount_binding.return_value = (72, 7)
        self.cache.unmount_cache.reset_mock()
        with self.assertRaises(ValueError): mount.worker("/private/records", self.sha, "unmount")
        self.cache.unmount_cache.assert_not_called()

    def attach_setup(self):
        self.cache.directory.return_value = mock.MagicMock()
        self.cache.directory.return_value.__enter__.return_value = 99
        self.stack.enter_context(mock.patch.object(mount.os, "scandir", return_value=mock.MagicMock(
            __enter__=mock.Mock(return_value=iter(())))))
        self.stack.enter_context(mock.patch.object(mount.os, "fstat", return_value=SimpleNamespace(st_dev=5, st_ino=61)))

    def test_intent_publication_failure_prevents_mount(self):
        self.attach_setup()
        self.records.publish.side_effect = OSError("synthetic journal failure")
        with self.assertRaises(OSError): mount.worker("/private/records", self.sha, "attach")
        self.cache.mount_cache.assert_not_called()

    def test_failed_created_publication_preserves_mount_and_never_guesses_cleanup(self):
        self.attach_setup()
        calls = []
        def publish(name, _):
            calls.append(name)
            if name == "mount-created.json": raise OSError("synthetic fsync failure")
        self.records.publish.side_effect = publish
        self.cache.mount_cache.side_effect = lambda *_args, **_kwargs: calls.append("mount")
        with self.assertRaises(OSError): mount.worker("/private/records", self.sha, "attach")
        self.assertEqual(calls, ["mount-intent.json", "mount", "mount-created.json"])
        self.cache.unmount_cache.assert_not_called()


if __name__ == "__main__":
    unittest.main()
