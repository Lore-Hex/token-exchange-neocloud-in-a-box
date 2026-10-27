# SPDX-License-Identifier: Apache-2.0
"""Weekly serving leases use the same owner, drain and fail-closed boundaries."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_selected_service_lifetime as prior

service, network, host, smoke = prior.service, prior.network, prior.host, prior.smoke
WEEK = service.WEEKLY_PROFILE
MILLION = service.engine_entry.MILLION_PROFILE


class WeeklyLeaseTests(unittest.TestCase):
    setUp = prior.LifetimeTests.setUp
    contract = prior.LifetimeTests.contract
    supervisor = prior.LifetimeTests.supervisor

    def test_explicit_weekly_bound_and_unchanged_default(self):
        contract = self.contract(604800, WEEK, execution_profile=MILLION)
        self.assertEqual(contract.hard_monotonic, self.clock.mono + 604800)
        for seconds, profile, execution in ((604801, WEEK, MILLION), (86401, prior.DAY, MILLION),
                (4801, service.SMOKE_PROFILE, MILLION), (0, WEEK, MILLION),
                (604800, WEEK, service.engine_entry.EAGER_PROFILE)):
            with self.subTest(seconds=seconds, profile=profile), self.assertRaises(ValueError):
                self.contract(seconds, profile, execution_profile=execution)
        with self.assertRaises(service.pilot.pilot.PilotError):
            service.pilot.retirement_deadline(self.clock.timestamp(604800), public=True)

    def test_lifetime_reaches_guard_and_receipt_without_extending_requests(self):
        supervisor, backend = self.supervisor(WEEK)
        contract = supervisor.contract
        self.assertEqual((contract.request_seconds, contract.cleanup_reserve), (3540, 7240))
        args = contract.guard_arguments(supervisor.inputs)
        self.assertEqual(args[-2:], ("--lifetime-profile", WEEK))
        self.assertEqual(args[args.index("--retirement-at") + 1], contract.admission_stop_at)
        backend.observation = service.EngineReady(prior.lifecycle_tests.endpoint(1000),
            json.dumps({"data": [{"id": service.MODEL, "max_model_len": 1048576}]}).encode(),
            1048576, service.MEMORY_BYTES, 0, MILLION)
        result = supervisor.run()
        self.assertEqual(result["lifetime"], contract.lifetime)
        self.assertEqual(result["lifetime"]["profile"], WEEK)
        self.assertTrue(result["cleanup_confirmed"])
        self.assertEqual(len(backend.launches), 3)
        self.assertFalse(result["confidential"] or result["deployable"])
        self.assertLessEqual(self.clock.mono, contract.hard_monotonic)

    def test_clock_jumps_and_smoke_conflicts_remain_fail_closed(self):
        supervisor, backend = self.supervisor(WEEK)
        self.clock.wall += 500000
        short = supervisor.cutoff()
        self.clock.wall -= 400000
        self.assertEqual(supervisor.cutoff(), short)
        for mode in ("inference_smoke", "conformance_smoke", "protocol_smoke", "tool_smoke"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                supervisor.run(**{mode: True})
        self.assertEqual(backend.launches, [])
        with self.assertRaises(ValueError):
            service.qualification_options(True, WEEK, MILLION)

    def test_network_journal_binds_weekly_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "network").mkdir(mode=0o700)
            journal = network.Journal(root / "network", prior.network_tests.network(),
                self.clock.wall + 604800, self.clock.mono + 604800, lifetime_profile=WEEK)
            journal.initialize()
            with mock.patch.object(network.tau_runtime, "time", self.clock):
                restored = host.read_reservation(root)
            self.assertEqual(restored.contract, journal.contract)
            self.assertEqual(restored.lifetime_profile, WEEK)

    def test_weekly_receipt_cannot_join_a_day_lease(self):
        lifetime = service.lifetime_record(WEEK, self.clock.wall + 604800)
        row = {**prior.smoke_tests.lifecycle(), "schema": service.ORDINARY_SCHEMA,
               "lifetime": lifetime, "prepared_sha256": "a" * 64}
        raw = json.dumps(row).encode()
        smoke.ordinary_lifecycle(raw, lifetime, "a" * 64, failed=False)
        changed = copy.deepcopy(lifetime)
        changed["profile"] = prior.DAY
        with self.assertRaises(ValueError):
            smoke.ordinary_lifecycle(raw, changed, "a" * 64, failed=False)


if __name__ == "__main__":
    unittest.main()
