# SPDX-License-Identifier: Apache-2.0
"""Synthetic one-lifetime joins and original-byte replay; never GPU acceptance."""

from contextlib import ExitStack, contextmanager, redirect_stdout
import copy
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import selected_combined_owner as owner
import test_selected_combined_capacity as capacity_fixture
import test_selected_long_qualification as long_fixture
import test_selected_service as lifecycle_fixture
import test_stream_scaling as scaling_fixture
from test_stream_cohort import workload

receipt, service, process, long, scaling = owner.receipt, owner.service, owner.process, owner.long, owner.scaling
SHA = "a" * 64


def clocks(clock):
    return dict(preparation_seconds=300, now_wall=clock.time(), now_monotonic=clock.monotonic(),
                hard_wall=clock.time() + 10000, hard_monotonic=clock.monotonic() + 10000)


def qualified(identity, *, failed=None):
    row = long.empty_report(owner.plan.PROFILE)
    row.update(prepared_sha256=SHA, request_seconds=900, cleanup_reserve_seconds=1960,
        workload=long_fixture.workload(), engine_identity_before_sha256=identity, elapsed_seconds=50.)
    def collect(*args):
        observed = long_fixture.collector(*args)
        if failed == "cancelled":
            args[-1]["attempted"] = True
            raise KeyboardInterrupt("PRIVATE_CANARY")
        if args[1] == 3:
            return replace(observed, sample=replace(observed.sample, completion_tokens=long.LONG_OUTPUT, finish_reason="length"))
        if failed == "cache":
            return replace(observed, sample=replace(observed.sample, cached_prompt_tokens=0))
        return observed
    try:
        long.sequence(row, row["workload"], collect, deadline=100, clock=lambda: 0)
        row.update(result="completed", failure="none", engine_identity_after_sha256=identity)
    except BaseException as exc:
        row["failure"] = long.failure(exc)
    long.summarize(row)
    return long.validate_combined_report(long.encoded(row), SHA)


def scaled(identity, *, failure=None):
    row, _ = scaling_fixture.ScalingTests().execute(failure=failure)
    row.update(schema="nib.selected-combined-scaling/v1", model=service.MODEL,
        prepared_sha256=SHA, engine_identity_sha256=identity)
    observation = capacity_fixture.parse(capacity_fixture.metrics())
    row["capacity"] = observation
    for plateau in row["plateaus"]:
        if plateau["capacity_after"] is not None:
            plateau["capacity_after"] = copy.deepcopy(observation)
    assert scaling.validate_combined(row) == row["completed"]
    return row


class OwnerTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with lifecycle_fixture.LifecycleTests().setup_service() as (supervisor, fake, clock, shutdown), ExitStack() as stack:
            stack.enter_context(mock.patch.object(owner, "time", clock))
            alarm = service.identity.worker.alarm
            stack.enter_context(mock.patch.object(service.identity.worker, "alarm",
                side_effect=lambda deadline: alarm(deadline, clock=clock.monotonic)))
            run = owner.Run(SHA, clocks(clock))
            contract = service.Contract.begin(clock.timestamp(8560), input_price="1", output_price="2",
                lifetime_profile=service.ORDINARY_PROFILE, execution_profile=owner.BASE_PROFILE)
            supervisor.contract = replace(contract, startup_deadline=run.deadline("startup"))
            supervisor.hard_deadline = supervisor.contract.hard_monotonic
            prepared = object.__new__(process.Prepared)
            prepared.receipt_sha256 = SHA
            prepared.lab = {"execution_profile": owner.BASE_PROFILE, "paths": ["/synthetic-vllm"]}
            concrete = object.__new__(process.ProcessOwner)
            concrete.prepared, concrete.combined = prepared, run
            concrete.identities = {role: dict(lifecycle_fixture.endpoint(1000 + index).expected)
                                   for index, role in enumerate(service.ROLES)}
            concrete.processes, concrete.shutdown = fake.children, shutdown
            for name in ("start", "probe", "check_mounts", "drain", "reap", "reap_unregistered"):
                stack.enter_context(mock.patch.object(concrete, name, getattr(fake, name)))
            fake.prepared = prepared
            fake.observation = service.EngineReady(lifecycle_fixture.endpoint(1000),
                receipt.encoded({"data": [{"id": service.MODEL, "max_model_len": 131072}]}),
                131072, service.MEMORY_BYTES, 0, owner.BASE_PROFILE)
            supervisor.owner = concrete
            run.workload = workload()
            def checkpoint(actual, identities, deadline):
                actual.checkpoint(deadline)
                self.assertEqual(actual.owner.identities, identities)
            stack.enter_context(mock.patch.object(long.inference, "checkpoint", side_effect=checkpoint))
            stack.enter_context(mock.patch.object(owner.capacity, "verify_sources"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            yield supervisor, run, fake, clock, shutdown

    def execute(self, fault=None):
        with self.fixture() as (supervisor, run, fake, clock, shutdown):
            events = []
            short_report = scaled(long.digest(supervisor.owner.identities["engine"]),
                                  failure="lost" if fault == "short-partial-exception" else None)
            def long_stage(actual, **options):
                events.append("long")
                self.assertIs(actual, supervisor)
                self.assertEqual(options, {"stage_deadline": run.deadline("long"), "combined": True})
                if fault == "long-exception":
                    raise RuntimeError("PRIVATE_CANARY")
                row = qualified(long.digest(run.identities["engine"]),
                    failed="cancelled" if fault == "long-partial-exception" else fault)
                if fault == "long-partial-exception":
                    run.long_row = row
                    raise RuntimeError("PRIVATE_CANARY")
                if fault == "source":
                    fake.mounts_changed = True
                if fault == "identity":
                    supervisor.owner.identities["engine"]["start_ticks"] += 1
                if fault == "deadline":
                    clock.advance(6000)
                return row
            def short_stage(*args, **options):
                events.append("throughput")
                row = copy.deepcopy(short_report)
                if fault == "short-cancelled":
                    row.update(completed=False, stop_reason="cancelled")
                    shutdown.stopped = True
                if fault == "reap":
                    fake.reap = lambda *_: False
                    supervisor.owner.reap = fake.reap
                for index in range(1 if fault == "short-partial-exception" else 4):
                    run.row["components"]["capacity-" + str(index)] = receipt.component(
                        "capacity-" + str(index), capacity_fixture.metrics())
                if fault == "short-partial-exception":
                    run.scaling_row = row
                    raise RuntimeError("PRIVATE_CANARY")
                return row
            with mock.patch.object(long, "run_owned", side_effect=long_stage), \
                    mock.patch.object(scaling, "run", side_effect=short_stage), \
                    mock.patch.object(owner, "print", create=True,
                        side_effect=OSError("PRIVATE_CANARY") if fault == "long-publication" else None):
                lifecycle = supervisor.run(combined=run)
            value = run.finish(lifecycle, shutdown)
            self.assertNotIn("PRIVATE_CANARY", receipt.encoded(value).decode())
            self.assertEqual(len([event for event in fake.events if event == ("start", "engine")]), 1)
            return value, events

    def test_one_engine_long_then_scaling_cleanup_and_original_replay(self):
        row, events = self.execute()
        self.assertEqual(events, ["long", "throughput"])
        self.assertEqual(row["result"], "pass")
        self.assertTrue(row["cleanup_confirmed"])
        self.assertFalse(row["hardware_qualified"] or row["public_admission_changed"])
        self.assertEqual(receipt.validate(receipt.encoded(row), SHA), row)

    def test_long_failure_source_identity_deadline_never_start_throughput(self):
        for fault in ("cache", "cancelled", "long-exception", "source", "identity", "deadline"):
            with self.subTest(fault=fault):
                row, events = self.execute(fault)
                self.assertEqual(events, ["long"])
                self.assertEqual(row["result"], "fail")
                self.assertNotIn("scaling", row["components"])
                if fault == "cancelled":
                    self.assertTrue(row["cancelled"])
                    self.assertEqual(row["requests_with_unknown_usage"], 1)

    def test_short_cancel_and_failed_reap_never_pass_or_erase_usage(self):
        for fault in ("short-cancelled", "reap"):
            with self.subTest(fault=fault):
                row, events = self.execute(fault)
                self.assertEqual(events, ["long", "throughput"])
                self.assertEqual(row["result"], "fail")
                self.assertGreater(row["known_prompt_tokens"], 0)
                self.assertIn("scaling", row["components"])

    def test_exact_owner_source_profile_and_mode_before_spawn(self):
        with self.fixture() as (supervisor, run, fake, _, _):
            for change in ("other-owner", "million", "smoke", "different-source"):
                original = supervisor.contract
                sha = supervisor.owner.prepared.receipt_sha256
                previous = supervisor.owner.combined
                if change == "other-owner": supervisor.owner.combined = None
                if change == "million": supervisor.contract = replace(original, execution_profile=process.engine_entry.MILLION_PROFILE)
                if change == "smoke": supervisor.contract = replace(original, lifetime_profile=service.SMOKE_PROFILE)
                if change == "different-source": supervisor.owner.prepared.receipt_sha256 = "b" * 64
                with self.subTest(change=change), self.assertRaises(ValueError):
                    supervisor.run(combined=run)
                self.assertEqual(fake.children, {})
                supervisor.contract = original
                supervisor.owner.prepared.receipt_sha256 = sha
                supervisor.owner.combined = previous

    def test_clock_rollback_does_not_renew_stage_or_cleanup(self):
        clock = lifecycle_fixture.Clock()
        with mock.patch.object(owner, "time", clock):
            run = owner.Run(SHA, clocks(clock))
            initial = run.deadline("long")
            clock.wall += 2000
            shortened = run.deadline("long")
            clock.wall -= 4000
            self.assertLessEqual(run.deadline("long"), shortened)
            self.assertLessEqual(shortened, initial)
            clock.advance(20000)
            self.assertLess(run.deadline("service-cleanup"), clock.monotonic())

    def test_partial_child_exception_and_unknown_dispatch_are_retained(self):
        for fault, unknown, events in (("long-partial-exception", 1, ["long"]),
                ("short-partial-exception", 16, ["long", "throughput"])):
            with self.subTest(fault=fault):
                row, observed = self.execute(fault)
                self.assertEqual(observed, events)
                self.assertEqual(row["result"], "fail")
                self.assertEqual(row["requests_with_unknown_usage"], unknown)
                self.assertTrue(row["cancelled"])
                self.assertIn("long", row["components"])
                if fault.startswith("short"):
                    self.assertGreater(row["known_output_tokens"], 0)
                    s = receipt.decode(receipt.original("scaling", row["components"]["scaling"]), 30 << 20)
                    self.assertEqual(s["dispatch_unknown_requests"], 16)
                    self.assertFalse(row["cleanup_confirmed"])

    def test_failure_publishing_long_payload_stops_throughput_without_erasing_usage(self):
        row, events = self.execute("long-publication")
        self.assertEqual(events, ["long"])
        self.assertEqual(row["result"], "fail")
        self.assertTrue(row["cleanup_confirmed"])
        self.assertGreater(row["known_output_tokens"], 0)
        self.assertTrue(long.validate_combined_report(receipt.original("long", row["components"]["long"]))["checks_pass"])

    def test_actual_image_entrypoint_delegates_only_the_closed_combined_mode(self):
        argv = ["--prepared-sha256", SHA, "--combined-qualification", "--preparation-seconds", "300"]
        with mock.patch.object(owner, "main", return_value=7) as main:
            self.assertEqual(process.main(argv), 7)
        main.assert_called_once_with(argv[:2] + argv[3:])

    def test_real_long_and_scaling_helpers_share_one_owner_and_retain_lost_wave(self):
        for lost in (False, True):
            with self.subTest(lost=lost), self.fixture() as (supervisor, run, fake, clock, shutdown), ExitStack() as stack:
                root = Path(__file__).absolute().parents[1]
                supervisor.owner.prepared.files = {name: process.digest((root / name).read_bytes())
                                                  for name in (long.SELF, long.CHILD)}
                stack.enter_context(mock.patch.object(process, "ROOT", root))
                stack.enter_context(mock.patch.object(long, "time", clock))
                stack.enter_context(mock.patch.object(scaling, "time", clock))
                stack.enter_context(mock.patch.object(long, "prepare_owned", return_value=long_fixture.workload()))
                sequence = long.sequence
                stack.enter_context(mock.patch.object(long, "sequence",
                    side_effect=lambda *args, **kw: sequence(*args, **kw, clock=clock.monotonic)))
                def collect(_supervisor, _identities, *args):
                    clock.advance(10)
                    value = long_fixture.collector(*args)
                    return replace(value, sample=replace(value.sample, completion_tokens=long.LONG_OUTPUT,
                        finish_reason="length")) if args[1] == 3 else value
                stack.enter_context(mock.patch.object(long, "collect", side_effect=collect))
                stack.enter_context(mock.patch.object(owner.capacity.capacity, "_probe_raw",
                                                      return_value=capacity_fixture.metrics()))
                def warmup(*_args, **_kwargs):
                    clock.advance(1)
                    return scaling.meter.Sample("completed", .5, .1, .2, .3, 2, 2048, 5, 0, "stop")
                stack.enter_context(mock.patch.object(scaling.stream.OwnedStreamHTTP, "collect", side_effect=warmup))
                def wave(endpoint, work, record, **kw):
                    self.assertEqual(kw["prepared_sha256"], SHA)
                    if lost:
                        raise KeyboardInterrupt("PRIVATE_CANARY")
                    start = clock.monotonic() - kw["epoch"]
                    count = kw["concurrency"]
                    clock.advance(30)
                    sample = scaling.meter.Sample("completed", 4., .4, .4, 2., 2, 2048, 5, 0, "stop").metadata()
                    record.update(start=start, end=start + 30, samples=[dict(sample) for _ in range(count)],
                        intervals=[{"index": i, "dispatch": start, "start": start, "end": start + 4,
                                    "reported": True, "exit_code": 0} for i in range(count)],
                        collectors_reaped=True, stop_reason="complete", peak_in_flight=count, dispatch_unknown_requests=0)
                stack.enter_context(mock.patch.object(scaling, "wave", side_effect=wave))
                lifecycle = supervisor.run(combined=run)
                row = run.finish(lifecycle, shutdown)
                self.assertEqual(row["result"], "fail" if lost else "pass")
                self.assertEqual(row["requests_with_unknown_usage"], 16 if lost else 0)
                self.assertEqual(row["cleanup_confirmed"], not lost)
                self.assertEqual(len([e for e in fake.events if e == ("start", "engine")]), 1)
                self.assertEqual(scaling.binding.digest(supervisor.owner.identities["engine"]), row["engine_identity_sha256"])


class ReceiptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.valid, _ = OwnerTests().execute()

    def test_same_owner_source_and_engine_join_rejects_substitution(self):
        for key, value in (("prepared_sha256", "b" * 64), ("engine_identity_sha256", "c" * 64),
                           ("terminal_identity_verified", False), ("context_tokens", 1048576),
                           ("cleanup_confirmed", False), ("public_max_inflight", 32),
                           ("known_output_tokens", 0), ("cancelled", True)):
            row = copy.deepcopy(self.valid)
            row[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                receipt.validate(receipt.encoded(row))

    def test_component_hash_size_duplicate_keys_and_legacy_separation(self):
        for change in ("hash", "size", "base64", "extra"):
            row = copy.deepcopy(self.valid)
            component = row["components"]["long"]
            if change == "hash": component["sha256"] = "c" * 64
            if change == "size": component["bytes"] += 1
            if change == "base64": component["base64"] += "="
            if change == "extra": component["PRIVATE_CANARY"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                receipt.validate(receipt.encoded(row))
        with self.assertRaises(ValueError): receipt.decode(b'{"x":1,"x":2}', 128)
        with self.assertRaises(ValueError): receipt.decode(b'{"x":NaN}', 128)
        raw = receipt.original("long", self.valid["components"]["long"])
        with self.assertRaises(ValueError): long.validate_report(raw)
        short = receipt.decode(receipt.original("scaling", self.valid["components"]["scaling"]), 30 << 20)
        with self.assertRaises(ValueError): scaling.validate(short, {}, scaling.cohort, service.MODEL, "isolated")

    def test_forged_mixed_stage_source_and_engine_are_rejected_with_correct_component_hashes(self):
        for name, key in (("long", "prepared_sha256"), ("long", "engine_identity_after_sha256"),
                          ("scaling", "prepared_sha256"), ("scaling", "engine_identity_sha256"),
                          ("lifecycle", "execution_prepared_sha256")):
            row = copy.deepcopy(self.valid)
            inner = receipt.decode(receipt.original(name, row["components"][name]), receipt.LIMITS[name])
            inner[key] = "d" * 64
            row["components"][name] = receipt.component(name, receipt.encoded(inner))
            with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                receipt.validate(receipt.encoded(row), SHA)

    def test_maximum_component_is_retained_without_truncation(self):
        row = copy.deepcopy(self.valid)
        original = receipt.original("scaling", row["components"]["scaling"])
        raw = original + b" " * (receipt.LIMITS["scaling"] - len(original))
        row["components"]["scaling"] = receipt.component("scaling", raw)
        encoded = receipt.encoded(row)
        self.assertLessEqual(len(encoded), receipt.MAX_RECEIPT)
        replay = receipt.validate(encoded, SHA)
        self.assertEqual(receipt.original("scaling", replay["components"]["scaling"]), raw)
        with self.assertRaises(ValueError): receipt.component("scaling", raw + b" ")

    def test_private_exclusive_write_partial_failure_and_replay(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "capture.jsonl"
            raw = receipt.encoded(self.valid) + b"\n"
            receipt.retain(path, raw, receipt.MAX_CAPTURE)
            self.assertEqual(receipt.replay(path, SHA), self.valid)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError): receipt.retain(path, b"replacement", 128)
            self.assertEqual(path.read_bytes(), raw)
            partial = Path(root) / "partial"
            with self.assertRaises(KeyboardInterrupt):
                with receipt.sink(partial) as stream:
                    stream.write(b"original partial bytes")
                    raise KeyboardInterrupt()
            self.assertEqual(partial.read_bytes(), b"original partial bytes")
            link = Path(root) / "link"
            link.symlink_to(path)
            with self.assertRaises(OSError): receipt.retain(link, b"wrong", 128)

    def test_progress_original_must_equal_final_component(self):
        progress = receipt.encoded({"schema": "nib.selected-combined-progress/v1",
            "prepared_sha256": SHA, "long": self.valid["components"]["long"]})
        raw = progress + b"\n" + receipt.encoded(self.valid) + b"\n"
        self.assertEqual(receipt.capture(raw, SHA), self.valid)
        with self.assertRaises(ValueError): receipt.capture(progress + b"\n" + raw, SHA)
        with self.assertRaises(ValueError): receipt.capture(progress + b"\n", SHA)


if __name__ == "__main__":
    unittest.main()
