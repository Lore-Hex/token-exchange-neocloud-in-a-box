# SPDX-License-Identifier: Apache-2.0
"""Offline audit of an intact interrupted cache-probe journal; never recovery."""

import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache_probe as probe

meter, require = probe.meter, probe.require
LIMIT = 64 << 10
COMMON = {"schema", "run_id", "kind"}
IDENTITY = {"index", "repeat", "phase"}
MEASUREMENT = {"status", "elapsed_seconds", "ttft_seconds", "first_content_seconds",
               "mean_output_token_interval_estimate_seconds", "output_events",
               "prompt_tokens", "completion_tokens", "cached_prompt_tokens", "finish_reason"}


def fields(row, kind, names, run_id):
    require(type(row) is dict and set(row) == COMMON | names
            and row["schema"] == "nib.cache-probe/v1" and row["kind"] == kind
            and row["run_id"] == run_id)


def measurement(value, prompt, output, attempted):
    require(type(value) is dict and set(value) == MEASUREMENT
            and value["status"] in meter.FAILURES | {"completed"}
            and meter.number(value["elapsed_seconds"])
            and meter.integer(value["output_events"], meter.MAX_STREAM)
            and value["finish_reason"] in (None, "stop", "length"))
    for name in ("ttft_seconds", "first_content_seconds", "mean_output_token_interval_estimate_seconds"):
        require(value[name] is None or meter.number(value[name])
                and value[name] <= value["elapsed_seconds"])
    incoming, outgoing, cached = (value[name] for name in
                                  ("prompt_tokens", "completion_tokens", "cached_prompt_tokens"))
    # A late protocol failure can retain totals that disagree with the prepared
    # tokenizer count. Keep those measured totals, never replace them with the plan.
    require((incoming is None and outgoing is None) or
            meter.integer(incoming, probe.stream.MAX_PROMPT) and
            meter.integer(outgoing, output) and outgoing > 0)
    require(cached is None or incoming is not None and meter.integer(cached, incoming))
    if value["status"] == "completed":
        require(attempted and incoming == prompt and outgoing is not None
                and value["output_events"] > 0 and value["ttft_seconds"] is not None
                and value["finish_reason"] is not None)
    else:
        require(value["ttft_seconds"] is None and
                value["mean_output_token_interval_estimate_seconds"] is None)
    if not attempted:
        require(incoming is outgoing is cached is None and value["output_events"] == 0
                and value["first_content_seconds"] is None and value["finish_reason"] is None
                and value["mean_output_token_interval_estimate_seconds"] is None)


def audit(raw):
    require(type(raw) is bytes and 0 < len(raw) <= LIMIT and raw.endswith(b"\n"))
    lines = raw.splitlines()
    require(1 <= len(lines) <= 1 + 2 * probe.REQUESTS)
    rows = [json.loads(line, object_pairs_hook=meter.unique,
                       parse_constant=lambda _: require(False)) for line in lines]
    manifest = rows[0]
    require(type(manifest) is dict and type(manifest.get("run_id")) is str
            and re.fullmatch("[0-9a-f]{64}", manifest["run_id"]))
    run_id = manifest["run_id"]
    fixed = {"concurrency": 1, "retries": 0, "planned_requests": probe.REQUESTS,
             "run_seconds_cap": probe.RUN_SECONDS}
    fields(manifest, "manifest", set(fixed) | {"prompt_tokens_per_request",
        "max_output_tokens_per_request", "reserved_total_tokens", "messages_sha256"}, run_id)
    prompt = manifest["prompt_tokens_per_request"]
    require(type(prompt) is int and prompt in probe.SHAPES)
    output = probe.SHAPES[prompt]
    fixed.update({"max_output_tokens_per_request": output,
                  "reserved_total_tokens": probe.REQUESTS * (prompt + output)})
    require(all(type(manifest[name]) is int and manifest[name] == expected for name, expected in fixed.items())
            and type(manifest["messages_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", manifest["messages_sha256"]))
    results, pending, stopped, unconfirmed = [], False, False, 0
    for row in rows[1:]:
        require(not stopped and len(results) < probe.REQUESTS and type(row) is dict)
        index = len(results)
        require(type(row.get("index")) is int and row["index"] == index
                and type(row.get("repeat")) is int and row["repeat"] == index // len(probe.PHASES)
                and row.get("phase") == probe.PHASES[index % len(probe.PHASES)])
        if row.get("kind") == "intent":
            fields(row, "intent", IDENTITY, run_id)
            require(not pending)
            pending = True
            continue
        fields(row, "result", IDENTITY | {"attempted", "measurement"}, run_id)
        require(type(row["attempted"]) is bool and (pending or not row["attempted"]))
        value = row["measurement"]
        measurement(value, prompt, output, row["attempted"])
        results.append(row)
        unconfirmed += int(pending and not row["attempted"])
        pending = False
        stopped = (value["status"] != "completed" or value["cached_prompt_tokens"] is None
                   or row["phase"] != "warm" and value["cached_prompt_tokens"] != 0)
    attempted = [row["measurement"] for row in results if row["attempted"]]
    uncertain = int(pending) + unconfirmed
    return {"schema": "nib.cache-journal-audit/v1", "run_id": run_id,
            "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
            "journal_records": len(rows), "planned_requests": probe.REQUESTS,
            "recorded_results": len(results), "recorded_attempted_results": len(attempted),
            "orphaned_intents": int(pending), "unconfirmed_presend_intents": unconfirmed,
            "started_or_uncertain_requests": len(attempted) + uncertain,
            "requests_without_send_evidence": probe.REQUESTS - len(attempted) - uncertain,
            "completed_result_records": sum(row["status"] == "completed" for row in attempted),
            "unknown_usage_requests": uncertain + sum(row["prompt_tokens"] is None for row in attempted),
            "unknown_cache_usage_requests": uncertain + sum(row["cached_prompt_tokens"] is None for row in attempted),
            "known_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in attempted),
            "known_completion_tokens": sum(row["completion_tokens"] or 0 for row in attempted),
            "known_cached_prompt_tokens": sum(row["cached_prompt_tokens"] or 0 for row in attempted),
            "prompt_count_mismatches": sum(row["prompt_tokens"] is not None and row["prompt_tokens"] != prompt
                                           for row in attempted),
            "terminal_present": False, "run_completed": False, "source_authenticated": False,
            "billing_reconciled": False, "accounting_holds_released": False,
            "cache_isolation_qualified": False, "tariff_change_justified": False,
            "quality_qualified": False, "confidential": False, "inference_performed": False}


def main():
    try:
        require(len(sys.argv) == 1)
        report = audit(sys.stdin.buffer.read(LIMIT + 1))
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, OSError):
        print("Cache journal audit rejected; preserve original evidence and unresolved holds.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
