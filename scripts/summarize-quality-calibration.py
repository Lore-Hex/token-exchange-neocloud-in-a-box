#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Publish numeric benchmark receipts; never copy arbitrary input text."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tau_trial as trial


def number(value):
    trial.require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
    return value


def score_fields(suite, value):
    trial.require(type(value) is dict)
    if suite == "ifeval":
        trial.require(set(value) == {"prompt_strict", "prompt_loose", "instruction_strict", "instruction_loose", "instructions"})
        trial.require(all(type(value[key]) is bool for key in ("prompt_strict", "prompt_loose")))
        trial.require(all(type(value[key]) is int for key in ("instruction_strict", "instruction_loose", "instructions")))
        n = value["instructions"]
        trial.require(1 <= n <= 100 and 0 <= value["instruction_strict"] <= value["instruction_loose"] <= n
                      and value["prompt_strict"] == (value["instruction_strict"] == n)
                      and value["prompt_loose"] == (value["instruction_loose"] == n))
    else:
        trial.require(set(value) == {"correct", "total", "errors", "score"} |
                      ({"no_answer"} if suite == "mmlu_pro" else set()))
        trial.require(type(value["total"]) is int and value["total"] == 1
                      and all(type(value[key]) is int and value[key] in (0, 1) for key in
                              ("correct", "errors", *( ["no_answer"] if suite == "mmlu_pro" else []))))
        trial.require(value["correct"] + value["errors"] + value.get("no_answer", 0) <= 1
                      and number(value["score"]) == value["correct"] * 100)
    return dict(value)


def timestamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def report(raw):
    rows = [json.loads(line) for line in raw.splitlines()]
    manifest, summary = rows[0], rows[-1]
    trial.require(manifest["schema"] == "nib.quality-calibration/v1" and summary["kind"] == "summary"
                  and manifest["suite"] in ("gsm8k", "mmlu_pro", "ifeval"))
    items = rows[1:-1]
    expected = manifest["expected"]
    trial.require(type(expected) is int and expected in (20, 50, 200) and len(items) <= expected)
    clean = []
    for index, row in enumerate(items):
        trial.require(row["kind"] == "item" and row["index"] == index and row["status"] in
                      ("evaluated", "truncated", "protocol_or_transport_error", "timeout", "cancelled"))
        score = {} if row["score"] is None else score_fields(manifest["suite"], row["score"])
        usage = row["usage"]
        trial.require(usage is None or type(usage) is dict and set(usage) == {"prompt_tokens", "completion_tokens"}
                      and all(type(value) is int and 0 <= value < 2**31 for value in usage.values()))
        trial.require(row["status"] not in ("evaluated", "truncated") or score and usage is not None)
        trial.require(row["status"] != "truncated" or not any(score.get(key, 0) for key in
                      ("correct", "prompt_strict", "prompt_loose", "instruction_strict", "instruction_loose")))
        clean.append({"index": index, "status": row["status"], "score": score, "usage": usage,
                      "elapsed_seconds": number(row["elapsed_seconds"])})
    evaluated = sum(row["status"] == "evaluated" for row in clean)
    trial.require(summary["expected"] == expected and summary["attempted"] == len(clean)
                  and summary["evaluated"] == evaluated and summary["missing"] == expected - len(clean))
    hashes = {key: manifest[key] for key in ("git_sha", "trbench_revision", "dataset_sha256",
                                             "trbench_tree_sha256", "endpoint_binding_sha256")}
    trial.require(all(type(value) is str and re.fullmatch("[0-9a-f]{%d}" % (40 if key in
        ("git_sha", "trbench_revision") else 64), value) for key, value in hashes.items()))
    return {"schema": "nib.quality-calibration-summary/v1", "provenance": hashes,
            "source_receipt_sha256": hashlib.sha256(raw).hexdigest(), "suite": manifest["suite"],
            "expected": expected, "evaluated": evaluated, "missing": expected - len(clean),
            "errors": len(clean) - evaluated, "items": clean,
            "correct": sum(r["score"].get("correct", 0) for r in clean if r["status"] == "evaluated"),
            "prompt_strict": sum(r["score"].get("prompt_strict", 0) for r in clean if r["status"] == "evaluated"),
            "prompt_loose": sum(r["score"].get("prompt_loose", 0) for r in clean if r["status"] == "evaluated"),
            "started_utc": timestamp(manifest["started_at"]), "ended_utc": timestamp(summary["ended_at"]),
            "result": "pass" if evaluated == expected else "fail", "confidential": False,
            "full_benchmark": False, "matched_reference": False, "quality_qualified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trbench-root", type=Path, required=True)
    args = parser.parse_args()
    trial.require(sys.flags.isolated and args.input.stat().st_size <= 1 << 20)
    record = report(args.input.read_bytes())
    calibration = trial.load("scripts/record-quality-calibration.py")
    trial.require(calibration.source(args.trbench_root, record["provenance"]["trbench_revision"])
                  == record["provenance"]["trbench_tree_sha256"])
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=10).decode().strip()
    committed = subprocess.check_output(["git", "-C", str(root), "show", revision + ":scripts/" + Path(__file__).name], timeout=10)
    trial.require(committed == Path(__file__).read_bytes())
    envelope = trial.evidence_record(revision, record["source_receipt_sha256"], record["started_utc"], record)
    envelope.update(command="python3 -I -B scripts/summarize-quality-calibration.py --input PRIVATE --output EVIDENCE --trbench-root PRIVATE",
        notes="Scored selected cases using pinned TrustedRouter formatters and graders against a retained ordinary-TLS "
              "GPU endpoint. Pass means every selected case was evaluated, not every answer correct. "
              "No full-suite, matched-reference, public-listing or confidential qualification.")
    trial.publish_evidence(args.output, {"run.json": trial.tau.encoded(envelope), "output.log": trial.tau.encoded(record)})
    print(json.dumps({key: record[key] for key in ("suite", "expected", "evaluated", "correct", "prompt_strict", "prompt_loose")}))


if __name__ == "__main__":
    raise SystemExit(main())
