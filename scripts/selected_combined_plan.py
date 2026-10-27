# SPDX-License-Identifier: Apache-2.0
"""Pure candidate composition and fixed deadline arithmetic.

This module performs no I/O or admission. The separate selected_combined_owner
binds these calculations to source, process identity, stage results and cleanup;
its private CLI is not a public execution-profile registration or authorization.
"""

from dataclasses import dataclass, replace
import math

import deepseek_tuning as tuning

PROFILE = "long128k-batch32-graphs"
# Pinned to the existing implementations; cross-module tests detect drift.
# engine_lab.STARTUP_SECONDS; selected_long_qualification.RUN_SECONDS;
# stream_scaling.SECONDS; selected_service.cleanup_reserve(long128k-graphs);
# host/record-selected-service-smoke.py HOST_RESERVE.
STAGES = (("startup", 1800), ("long", 3600), ("throughput", 900),
          ("service-cleanup", 1960), ("host-cleanup", 180))
BASE_SECONDS = sum(seconds for _, seconds in STAGES)  # 8440, excluding preparation.
MAX_LIFETIME_SECONDS = 86400  # Existing ordinary-24h ceiling, not a new lab limit.


def require(value):
    if not value:
        raise ValueError("combined qualification plan rejected")


def profile(value):
    require(type(value) is str and value == PROFILE)


def number(value):
    return type(value) in (int, float) and 0 <= value < 2**40 and math.isfinite(value)


def candidate_arguments(baseline, *, execution_profile):
    """Preview only: caller supplies canonical lab argv, not arbitrary overrides.

    Reuse both closed transforms instead of re-specifying graph configuration.
    This does not authenticate the baseline or create a launchable lab contract.
    """
    profile(execution_profile)
    require(type(baseline) is list and all(type(item) is str for item in baseline))
    return tuning.combined_arguments(baseline)


def stage_budgets(preparation_seconds):
    # Deliberately no default: the 8440s floor does not account for all owner
    # preflight, hash checks, short-workload preparation and child reaping.
    require(type(preparation_seconds) is int
            and 0 < preparation_seconds <= MAX_LIFETIME_SECONDS - BASE_SECONDS)
    return (("preparation", preparation_seconds), *STAGES)


@dataclass(frozen=True)
class DeadlinePlan:
    """Pure arithmetic on caller-captured clocks; never reads or changes a clock.

    Stage ends are fixed at creation. An observation can only shorten the
    original cutoff; it cannot move unused time to a later stage or renew it.
    The executing owner enforces these ceilings; this value alone admits nothing.
    """
    preparation_seconds: int
    started_monotonic: float
    hard_wall: float
    original_hard_monotonic: float
    cutoff_monotonic: float
    observed_monotonic: float

    @classmethod
    def begin(cls, *, execution_profile, preparation_seconds, now_wall, now_monotonic,
              hard_wall, hard_monotonic):
        profile(execution_profile)
        phases = stage_budgets(preparation_seconds)
        require(all(number(value) for value in (now_wall, now_monotonic, hard_wall, hard_monotonic)))
        remaining = min(hard_wall - now_wall, hard_monotonic - now_monotonic)
        require(sum(seconds for _, seconds in phases) <= remaining
                and max(hard_wall - now_wall, hard_monotonic - now_monotonic) <= MAX_LIFETIME_SECONDS)
        return cls(preparation_seconds, now_monotonic, hard_wall, hard_monotonic,
                   now_monotonic + remaining, now_monotonic)

    def observe(self, *, now_wall, now_monotonic):
        require(number(now_wall) and number(now_monotonic) and now_monotonic >= self.observed_monotonic)
        return replace(self, observed_monotonic=now_monotonic,
                       cutoff_monotonic=min(self.cutoff_monotonic, now_monotonic + self.hard_wall - now_wall))

    def deadline(self, stage):
        phases = stage_budgets(self.preparation_seconds)
        require(type(stage) is str and stage in dict(phases))
        index = [name for name, _ in phases].index(stage)
        end = self.started_monotonic + sum(seconds for _, seconds in phases[:index + 1])
        reserve = sum(seconds for _, seconds in phases[index + 1:])
        return min(end, self.cutoff_monotonic - reserve)
