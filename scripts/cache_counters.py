# SPDX-License-Identifier: Apache-2.0
"""Offline, content-free DP2 prefix-cache counter snapshot; not billing evidence."""

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stream_capacity as capacity

METRICS = {"vllm:prefix_cache_queries_total": "queried_tokens",
           "vllm:prefix_cache_hits_total": "cached_tokens"}
NUMBER = re.compile(r"[+]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,4})?")


def parse(raw, model):
    require = capacity.require
    require(type(raw) is bytes and 0 < len(raw) <= capacity.LIMIT
            and type(model) is str and 0 < len(model) <= 256
            and "\r" not in model and "\n" not in model)
    rows = [{"rank": rank} for rank in (0, 1)]
    seen = set()
    for line in raw.decode("utf-8").splitlines():
        # Ignore unrelated series, but reject malformed forms of either counter.
        if not any(re.match(re.escape(name) + r"(?=\{|\s|$)", line) for name in METRICS):
            continue
        match = capacity.LINE.fullmatch(line)
        require(match is not None and match[2] is not None)
        name, labels = match[1], capacity.labels(match[2])
        require(set(labels) == {"engine", "model_name"}
                and labels["model_name"] == model and labels["engine"] in ("0", "1")
                and (name, labels["engine"]) not in seen)
        seen.add((name, labels["engine"]))
        require(len(match[3]) <= 128 and NUMBER.fullmatch(match[3]) is not None)
        value = Decimal(match[3])
        require(0 <= value < 2**53 and value == value.to_integral_value())
        rows[int(labels["engine"])][METRICS[name]] = int(value)
    require(len(seen) == 4 and all(row["cached_tokens"] <= row["queried_tokens"] for row in rows))
    totals = {key: sum(row[key] for row in rows) for key in METRICS.values()}
    require(all(value < 2**53 for value in totals.values()))
    return {
        "schema": "nib.cache-counter-snapshot/v1",
        "source_metrics_sha256": hashlib.sha256(raw).hexdigest(),
        "upstream_logger_sha256": capacity.SOURCES["vllm/v1/metrics/loggers.py"],
        "ranks": rows, **totals,
        "cached_fraction": (totals["cached_tokens"] / totals["queried_tokens"]
                            if totals["queried_tokens"] else None),
        "scope": "engine-lifetime-cumulative", "snapshot_atomic": False,
        "source_identity_verified": False, "customer_cache_rate_verified": False,
        "billing_reconciled": False, "performance_qualified": False,
        "tariff_change_justified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    try:
        result = parse(sys.stdin.buffer.read(capacity.LIMIT + 1), args.model)
    except Exception:
        parser.exit(1, "cache counter input rejected\n")
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
