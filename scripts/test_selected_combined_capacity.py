# SPDX-License-Identifier: Apache-2.0
"""Synthetic capacity observations only; never starts a collector or an engine."""

from contextlib import ExitStack
import copy
import hashlib
import unittest
from unittest import mock

import selected_combined_capacity as combined
import selected_combined_plan as plan
import stream_capacity as legacy
import test_stream_scaling as old


def metrics(tokens=262144, concurrency="2.0"):
    return (old.metrics().replace(old.MODEL.encode(), combined.service.MODEL.encode())
            .replace(b'kv_cache_size_tokens="262144"', f'kv_cache_size_tokens="{tokens}"'.encode())
            .replace(b'kv_cache_max_concurrency="64.0"', f'kv_cache_max_concurrency="{concurrency}"'.encode()))


def parse(raw=None):
    return combined.parse(metrics() if raw is None else raw,
                          execution_profile=plan.PROFILE, model=combined.service.MODEL)


class CombinedCapacityTests(unittest.TestCase):
    def test_concurrency_is_measured_at_context_not_sequence_limit(self):
        for tokens, concurrency in ((131072, "1.0"), (262144, "2.0"), (147456, "1.125")):
            value = parse(metrics(tokens, concurrency))
            self.assertEqual(value["ranks"][0]["kv_capacity_tokens"], tokens)
            self.assertEqual(value["ranks"][0]["kv_max_concurrency"], float(concurrency))
            self.assertEqual(value["public_max_inflight"], 1)
            self.assertEqual(value["engine_max_sequences"], 32)
            self.assertFalse(value["runtime_qualified"] or value["confidentiality_verified"])
            combined.stable(value, value)
            with self.assertRaises(ValueError): legacy.parse(metrics(tokens, concurrency), combined.service.MODEL)

    def test_old_capacity_and_receipt_are_unchanged_not_relabelled(self):
        self.assertEqual(legacy.parse(old.metrics(), old.MODEL), old.capacities())
        legacy.stable(old.capacities(), old.capacities())
        # The old 4K denominator cannot masquerade as the new 128K denominator.
        with self.assertRaises(ValueError): parse(metrics(262144, "64.0"))
        with self.assertRaises(ValueError): combined.stable(old.capacities(), old.capacities())
        with self.assertRaises(ValueError): legacy.stable(parse()["ranks"], parse()["ranks"])

    def test_insufficient_inconsistent_nonfinite_offload_and_busy_rejected(self):
        invalid = [metrics(131071, "1.0"), metrics(131072, "0.999"), metrics(262144, "2.00001"),
                   metrics(262145, "2.0"), metrics(262144, "nan"), metrics(262144, "inf")]
        raw = metrics()
        invalid += [raw + raw, raw.replace(b'engine="1"', b'engine="2"'),
                    raw.replace(combined.service.MODEL.encode(), b"unknown"),
                    raw.replace(b'kv_offloading_size="None"', b'kv_offloading_size="1"'),
                    raw.replace(b'cache_dtype="fp8"', b'cache_dtype="fp16"'),
                    raw.replace(b'} 0\n', b'} 1\n'), raw.splitlines()[0] + b"\n"]
        for raw in invalid:
            with self.subTest(sha=hashlib.sha256(raw).hexdigest()), self.assertRaises(ValueError): parse(raw)

    def test_policy_flags_and_rank_drift_fail_closed(self):
        before = parse()
        for name, bad in (("schema", "nib.old/v1"), ("execution_profile", "batch32-graphs"),
                          ("model", "unknown"), ("public_max_inflight", 32),
                          ("context_tokens", 4096), ("runtime_qualified", True),
                          ("confidentiality_verified", 0), ("public_max_inflight", True)):
            after = copy.deepcopy(before); after[name] = bad
            with self.subTest(field=name), self.assertRaises(ValueError): combined.stable(before, after)
        for name, bad in (("preemptions", 1), ("kv_capacity_tokens", 131072),
                          ("kv_max_concurrency", 1.0), ("running", 1), ("waiting", 1),
                          ("graph_capture_observed", True), ("rank", True), ("kv_fraction", float("nan"))):
            after = copy.deepcopy(before); after["ranks"][0][name] = bad
            with self.subTest(field=name), self.assertRaises(ValueError): combined.stable(before, after)
            with self.subTest(before=name), self.assertRaises(ValueError): combined.stable(after, before)
        for value in (None, [], {}, {**before, "unbound": True}):
            with self.assertRaises(ValueError): combined.stable(before, value)

    def test_closed_profile_and_no_runtime_enablement(self):
        for profile in (None, True, {}, "batch32-graphs", "long128k-graphs"):
            with self.assertRaises(ValueError): combined.parse(metrics(), execution_profile=profile, model=combined.service.MODEL)
        with self.assertRaises(ValueError): combined.parse(metrics(), execution_profile=plan.PROFILE, model=old.MODEL)
        self.assertNotIn(plan.PROFILE, plan.tuning.NAMES)
        self.assertNotIn(plan.PROFILE, combined.service.engine_entry.BOUND_PROFILES)
        with ExitStack() as stack:
            for target in ("builtins.open", "socket.socket", "subprocess.Popen", "time.time", "time.monotonic"):
                stack.enter_context(mock.patch(target, side_effect=AssertionError("unexpected I/O")))
            value = parse(); combined.stable(value, value)

    def test_upstream_source_closure_and_tamper(self):
        raw = b"synthetic source"; digest = hashlib.sha256(raw).hexdigest()
        paths = []
        def read(path, limit):
            paths.append(path); self.assertEqual(limit, 256 << 10); return raw
        with mock.patch.object(legacy, "SOURCES", {p: digest for p in legacy.SOURCES}), \
             mock.patch.object(combined, "SOURCES", {p: digest for p in combined.SOURCES}):
            combined.verify_sources(read, "/fixed")
            self.assertEqual(set(paths), {"/fixed/" + p for p in (*legacy.SOURCES, *combined.SOURCES)})
            with self.assertRaises(ValueError): combined.verify_sources(lambda *_: raw + b"bad", "/fixed")


if __name__ == "__main__":
    unittest.main()
