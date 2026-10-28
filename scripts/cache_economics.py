# SPDX-License-Identifier: Apache-2.0
"""Offline tariff sensitivity from numeric usage; never billing or qualification."""

from fractions import Fraction
import hashlib
import json
import re
import sys

LIMIT = 2 << 20
SCALE = 10**12


def require(value):
    if not value:
        raise ValueError("cache economics input rejected")


def integer(value, maximum):
    return type(value) is int and 0 <= value <= maximum


def decimal(value, maximum):
    require(type(value) is str and len(value) <= 24
            and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?", value))
    number = Fraction(value)
    require(number <= maximum)
    return number


def bounds(low, high):
    """Outward-rounded decimal bounds, including negative allocation differences."""
    require(low <= high)
    floor = low * SCALE
    ceil = high * SCALE
    def text(number):
        sign = "-" if number < 0 else ""
        whole, fractional = divmod(abs(number), SCALE)
        return f"{sign}{whole}.{fractional:012d}"
    return {"lower": text(floor.numerator // floor.denominator),
            "upper": text(-(-ceil.numerator // ceil.denominator))}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def analyze(value):
    require(type(value) is dict and set(value) == {
        "schema", "measurement_source_sha256", "window_seconds", "fleet_usd_per_hour",
        "input_usd_per_million", "output_usd_per_million", "cache_rates_usd_per_million",
        "max_prompt_tokens", "max_output_tokens", "planned_requests", "not_started_requests", "requests"}
        and value["schema"] == "nib.cache-economics-input/v1"
        and type(value["measurement_source_sha256"]) is str
        and re.fullmatch("[0-9a-f]{64}", value["measurement_source_sha256"]))
    seconds = decimal(value["window_seconds"], 7 * 86400)
    hourly = decimal(value["fleet_usd_per_hour"], 1000000)
    incoming = decimal(value["input_usd_per_million"], 1000000)
    outgoing = decimal(value["output_usd_per_million"], 1000000)
    rates = value["cache_rates_usd_per_million"]
    require(seconds > 0 and type(rates) is list and 1 <= len(rates) <= 8)
    cache = [decimal(rate, incoming) for rate in rates]
    require(len(set(cache)) == len(cache))
    prompt_cap, output_cap = value["max_prompt_tokens"], value["max_output_tokens"]
    require(integer(prompt_cap, 1048576) and prompt_cap > 0
            and integer(output_cap, 393216) and output_cap > 0
            and integer(value["planned_requests"], 10000) and value["planned_requests"] > 0
            and integer(value["not_started_requests"], value["planned_requests"]))
    rows = value["requests"]
    require(type(rows) is list and len(rows) + value["not_started_requests"] == value["planned_requests"])
    for row in rows:
        require(type(row) is dict and set(row) == {
            "status", "prompt_tokens", "completion_tokens", "cached_prompt_tokens"}
            and row["status"] in ("completed", "failed", "unresolved"))
        prompt, output, hit = row["prompt_tokens"], row["completion_tokens"], row["cached_prompt_tokens"]
        require((prompt is None and output is None) or
                integer(prompt, prompt_cap) and integer(output, output_cap))
        require(hit is None or prompt is not None and integer(hit, prompt))

    allocation = seconds * hourly / 3600
    def charge(row, rate):
        prompt, output, hit = row["prompt_tokens"], row["completion_tokens"], row["cached_prompt_tokens"]
        if prompt is None:
            return Fraction(0), (prompt_cap * incoming + output_cap * outgoing) / 1000000
        # Cache reads are a subset, never a surcharge on full-rate input.
        if hit is None:
            return ((prompt * rate + output * outgoing) / 1000000,
                    (prompt * incoming + output * outgoing) / 1000000)
        total = ((prompt - hit) * incoming + hit * rate + output * outgoing) / 1000000
        return total, total

    scenarios = []
    for label, rate in zip(rates, cache):
        completed = [Fraction(0), Fraction(0)]
        other = [Fraction(0), Fraction(0)]
        for row in rows:
            group = completed if row["status"] == "completed" else other
            low, high = charge(row, rate)
            group[0] += low
            group[1] += high
        scenarios.append({"cache_usd_per_million": label,
            "completed_request_gross_projection_usd": bounds(*completed),
            "failed_or_unresolved_tariff_equivalent_usd": bounds(*other),
            "completed_gross_minus_full_window_allocation_usd":
                bounds(completed[0] - allocation, completed[1] - allocation)})
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"schema": "nib.cache-economics/v1", "input_sha256": hashlib.sha256(encoded).hexdigest(),
        "measurement_source_sha256": value["measurement_source_sha256"],
        "planned_requests": value["planned_requests"], "started_or_uncertain_requests": len(rows),
        "not_started_requests": value["not_started_requests"],
        "completed_requests": sum(row["status"] == "completed" for row in rows),
        "failed_or_unresolved_requests": sum(row["status"] != "completed" for row in rows),
        "unknown_total_usage_requests": sum(row["prompt_tokens"] is None for row in rows),
        "unknown_cache_usage_requests": sum(row["cached_prompt_tokens"] is None for row in rows),
        "known_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in rows),
        "known_completion_tokens": sum(row["completion_tokens"] or 0 for row in rows),
        "known_cached_prompt_tokens": sum(row["cached_prompt_tokens"] or 0 for row in rows),
        "full_window_fleet_allocation_usd": bounds(allocation, allocation),
        "scenarios": scenarios, "measurement_source_verified": False,
        "settlement_verified": False, "marginal_cost_measured": False,
        "sustainable_tariff_qualified": False, "tariff_changed": False}


def main():
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        require(0 < len(raw) <= LIMIT)
        report = analyze(json.loads(raw, object_pairs_hook=unique))
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, OSError):
        print("Cache economics input rejected; no inference or pricing changes performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
