# SPDX-License-Identifier: Apache-2.0
"""Startup ceilings with fake clocks only; no inference or external calls."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from unittest import mock

import selected_service as service
import test_selected_service as lifecycles


PROFILE = service.engine_entry.ORIGINAL_MILLION_PROFILE


class OriginalStartupBudgetTests(unittest.TestCase):
    def begin(self, clock, seconds=18000, profile=PROFILE):
        return service.Contract.begin(clock.timestamp(seconds), input_price="1", output_price="2",
            lifetime_profile=service.ORDINARY_PROFILE, execution_profile=profile)

    def test_only_closed_original_profile_gets_larger_ceiling(self):
        self.assertEqual(service.startup_seconds(PROFILE), 7200)
        engine = service.engine_entry
        for profile in (engine.EAGER_PROFILE, *engine.BOUND_PROFILES):
            if profile != PROFILE:
                with self.subTest(profile=profile):
                    self.assertEqual(service.startup_seconds(profile), 1800)
        for value in (None, [], True, "original", "original-long1m-graphs-extra"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                service.startup_seconds(value)

    def test_deadline_captured_once_without_lifetime_or_drain_extension(self):
        clock = lifecycles.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            contract = self.begin(clock)
            legacy = self.begin(clock, profile=service.engine_entry.MILLION_PROFILE)
            self.assertEqual(contract.startup_deadline, clock.mono + 7200)
            self.assertEqual(legacy.startup_deadline, clock.mono + 1800)
            for field in ("hard_stop_at", "hard_wall", "hard_monotonic", "admission_stop_at",
                          "request_seconds", "cleanup_reserve"):
                self.assertEqual(getattr(contract, field), getattr(legacy, field), field)
            original = contract.startup_deadline
            clock.advance(3106)
            self.assertEqual(contract.startup_deadline, original)
            self.assertEqual(contract.startup_deadline - clock.mono, 4094)
            with self.assertRaises(FrozenInstanceError):
                contract.startup_deadline += 1

    def test_short_remaining_lifetime_keeps_all_cleanup_reserve(self):
        clock = lifecycles.Clock()
        with mock.patch.object(service, "time", clock), mock.patch.object(service.pilot, "time", clock):
            for available in (1, 100, 7199, 7200, 7201):
                with self.subTest(available=available):
                    contract = self.begin(clock, service.cleanup_reserve(PROFILE) + available)
                    self.assertEqual(contract.startup_deadline, clock.mono + min(available, 7200))
                    self.assertLessEqual(contract.startup_deadline,
                                         contract.hard_monotonic - contract.cleanup_reserve)

    def test_expired_startup_cannot_spawn_engine_even_with_lifetime_remaining(self):
        for elapsed in (7200, 7201):
            with self.subTest(elapsed=elapsed), lifecycles.LifecycleTests().setup_service() as (supervisor, owner, clock, _):
                supervisor.contract = self.begin(clock)
                supervisor.hard_deadline = supervisor.contract.hard_monotonic
                owner.prepared = SimpleNamespace(receipt_sha256="a" * 64)
                clock.advance(elapsed)
                result = supervisor.run()
                self.assertEqual(result["reason"], "deadline")
                self.assertFalse(result["ready"])
                self.assertEqual(owner.launches, [])
                self.assertTrue(result["cleanup_confirmed"])

    def test_wall_clock_forward_cannot_spend_cleanup_or_renew_startup(self):
        with lifecycles.LifecycleTests().setup_service() as (supervisor, _, clock, _):
            supervisor.contract = self.begin(clock)
            supervisor.hard_deadline = supervisor.contract.hard_monotonic
            startup = supervisor.contract.startup_deadline
            clock.wall += 11000
            shortened = supervisor.cutoff()
            clock.wall -= 22000
            self.assertEqual(supervisor.cutoff(), shortened)
            self.assertEqual(supervisor.contract.startup_deadline, startup)
            self.assertLess(shortened - supervisor.contract.cleanup_reserve, clock.mono)


if __name__ == "__main__":
    unittest.main()
