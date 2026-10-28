# SPDX-License-Identifier: Apache-2.0
"""Offline reconciliation of a recorded successful cache sequence, not admission."""

import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_journal as journal

probe, require, meter = journal.probe, journal.require, journal.meter


def audit(raw):
    require(type(raw) is bytes and 0 < len(raw) <= journal.LIMIT and raw.endswith(b"\n"))
    lines = raw.splitlines(keepends=True)
    require(len(lines) == 2 + 2 * probe.REQUESTS)
    # Reuse the interrupted-prefix validator without changing its conservative
    # accounting or teaching it to accept a terminal record.
    report = journal.audit(b"".join(lines[:-1]))
    require(report["recorded_results"] == report["recorded_attempted_results"] ==
            report["completed_result_records"] == probe.REQUESTS and
            all(report[name] == 0 for name in (
                "orphaned_intents", "unconfirmed_presend_intents", "unknown_usage_requests",
                "unknown_cache_usage_requests", "prompt_count_mismatches")))
    rows = [json.loads(line, object_pairs_hook=meter.unique,
                       parse_constant=lambda _: require(False)) for line in lines]
    results, summary = rows[2:-1:2], rows[-1]
    require(all(row["phase"] == "warm" or row["measurement"]["cached_prompt_tokens"] == 0
                for row in results))
    require(all(row["measurement"]["elapsed_seconds"] < probe.stream.REQUEST_SECONDS
                for row in results))
    expected = {
        "planned_requests": probe.REQUESTS, "attempted_requests": probe.REQUESTS,
        "not_started_requests": 0, "requests_with_unknown_usage": 0,
        "requests_with_unknown_cache_usage": 0,
        "known_prompt_tokens": report["known_prompt_tokens"],
        "known_completion_tokens": report["known_completion_tokens"],
        "known_cached_prompt_tokens": report["known_cached_prompt_tokens"],
        "warm_requests_with_reuse": sum(row["phase"] == "warm" and
            row["measurement"]["cached_prompt_tokens"] > 0 for row in results),
    }
    flags = {"completed": True, "billing_reconciled": False, "cache_isolation_qualified": False,
             "quality_qualified": False, "confidential": False, "tariff_change_justified": False}
    journal.fields(summary, "summary", set(expected) | set(flags) |
                   {"elapsed_seconds", "stop_reason"}, report["run_id"])
    require(all(type(summary[name]) is int and summary[name] == value
                for name, value in expected.items()) and
            all(summary[name] is value for name, value in flags.items()) and
            summary["stop_reason"] == "complete" and meter.number(summary["elapsed_seconds"]) and
            sum(row["measurement"]["elapsed_seconds"] for row in results) <=
            summary["elapsed_seconds"] < probe.RUN_SECONDS)
    report.pop("run_completed")
    return {**report, "schema": "nib.cache-complete-audit/v1",
            "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
            "journal_records": len(rows), "terminal_present": True,
            "recorded_sequence_completed": True,
            "elapsed_seconds": summary["elapsed_seconds"],
            "warm_requests_with_reuse": expected["warm_requests_with_reuse"]}


def main():
    try:
        require(len(sys.argv) == 1)
        report = audit(sys.stdin.buffer.read(journal.LIMIT + 1))
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, OSError):
        print("Complete cache journal rejected; preserve original evidence and unresolved holds.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
