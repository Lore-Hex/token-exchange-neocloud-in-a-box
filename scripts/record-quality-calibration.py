#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serial, content-free TR benchmark calibration against one pinned TLS node."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import statistics
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bounded_https
import tau_external_tokenizer
import tau_live_identity
import tau_selected_policy
import tau_trial_worker as worker

REVISION = "92c1f4dea58e32fa772f8ee77b69cd9d7cf67bac"
MODEL = tau_selected_policy.PUBLIC_MODEL
MAX_OUTPUT = 8192


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source(root, revision):
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=10).decode().strip()
    worker.require(head == revision)
    names = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "--name-only", revision,
                                     "--", "trbench", "uv.lock"], timeout=10).decode().splitlines()
    worker.require({"uv.lock", "trbench/__init__.py", "trbench/evals/gsm8k/score.py",
                    "trbench/evals/mmlu_pro/score.py", "trbench/evals/ifeval/vendor/evaluation_lib.py"} <= set(names))
    hashes = {}
    for name in names:
        path = root / name
        committed = subprocess.check_output(["git", "-C", str(root), "show", revision + ":" + name], timeout=10)
        worker.require(path.is_file() and not path.is_symlink() and path.read_bytes() == committed)
        hashes[name] = digest(committed)
    return digest(worker.encoded(hashes))


def cases(suite, root, count):
    sys.path.insert(0, str(root))
    if suite == "gsm8k":
        from trbench.evals.gsm8k import loader, run, score
        worker.require(loader.CACHE.is_file())
        rows = loader.load(limit=count)
        return rows, lambda row: row["question"] + run.PROMPT_SUFFIX, lambda row, text: score.score_model(
            [{"target": row["target"], "text": text}]), digest(loader.CACHE.read_bytes())
    if suite == "mmlu_pro":
        from trbench.evals.mmlu_pro import loader, run, score
        worker.require(loader.CACHE.is_file())
        rows = loader.load(limit=count)
        return rows, lambda row: run.PROMPT.format(question=row["question"], options=loader.format_options(row["options"])), \
            lambda row, text: score.score_model([{"answer": row["answer"], "text": text,
                                                 "n_options": len(row["options"])}]), digest(loader.CACHE.read_bytes())
    from trbench.evals.ifeval import run
    from trbench.evals.ifeval.vendor import evaluation_lib as evaluation
    rows = evaluation.read_prompt_list(str(run.DATA_PATH))[:count]
    import nltk
    import langdetect
    langdetect.DetectorFactory.seed = 0
    # Resources must already exist. No network/downloads while grading.
    nltk.data.find("tokenizers/punkt_tab/english/")
    def score(row, text):
        strict = evaluation.test_instruction_following_strict(row, {row.prompt: text})
        loose = evaluation.test_instruction_following_loose(row, {row.prompt: text})
        return {"prompt_strict": bool(strict.follow_all_instructions),
                "prompt_loose": bool(loose.follow_all_instructions),
                "instruction_strict": sum(strict.follow_instruction_list),
                "instruction_loose": sum(loose.follow_instruction_list),
                "instructions": len(row.instruction_id_list)}
    return rows, lambda row: row.prompt, score, digest(run.DATA_PATH.read_bytes())


def measured_usage(raw):
    worker.require(type(raw) is dict)
    usage = raw.get("usage", {})
    incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    worker.require(type(incoming) is int and type(outgoing) is int and 0 <= incoming < 2**31
                   and 0 <= outgoing < 2**31 and type(usage.get("total_tokens")) is int
                   and usage["total_tokens"] == incoming + outgoing)
    return {"prompt_tokens": incoming, "completion_tokens": outgoing}


def numeric_reply(raw, expected_prompt):
    usage = measured_usage(raw)
    worker.require(raw.get("model") == MODEL and usage["prompt_tokens"] == expected_prompt
                   and usage["completion_tokens"] <= MAX_OUTPUT)
    choices = raw.get("choices")
    worker.require(type(choices) is list and len(choices) == 1)
    choice = choices[0]
    worker.require(choice.get("finish_reason") in ("stop", "length"))
    message = choice["message"]
    worker.require(message.get("role") == "assistant" and type(message.get("content")) is str
                   and not message.get("tool_calls") and not message.get("reasoning_content"))
    return message["content"], choice["finish_reason"], usage


def write(stream, row):
    stream.write(worker.encoded(row))
    stream.flush()


def evaluate(index, item, prompt, grade, counter, http, key, deadline, row=None):
    if row is None:
        row = {}
    row.update(kind="item", index=index, status="infrastructure_error", stage="render",
               usage=None, score=None, http_status=None, generation_started=False)
    before = time.monotonic()
    http.last_status = None
    try:
        with worker.alarm(min(deadline, before + 119)):
            body = {"model": MODEL, "messages": [{"role": "user", "content": prompt(item)}],
                    "max_completion_tokens": MAX_OUTPUT, "temperature": 0,
                    "reasoning_effort": "none", "stream": False}
            row["stage"] = "tokenize"
            incoming = counter.count(body)
            worker.require(incoming + MAX_OUTPUT <= 32768)
            row["stage"] = "request"
            reply = http.post("/v1/chat/completions", body, key, min(deadline, before + 119),
                              before_send=lambda: row.update(generation_started=True))
            row["stage"] = "response-validation"
            row["usage"] = measured_usage(reply)
            text, finish, _ = numeric_reply(reply, incoming)
            row.update(finish_reason=finish, stage="grade")
            # Truncation never receives credit, but known token usage is retained.
            score = grade(item, text if finish == "stop" else "")
            worker.require(time.monotonic() < deadline)
            row.update(score=score, status="evaluated" if finish == "stop" else "truncated", stage="completed")
    except worker.Abort:
        row["status"] = "timeout"
    except KeyboardInterrupt:
        row["status"] = "cancelled"
    except Exception:
        row["status"] = "protocol_or_transport_error"
    row.update(elapsed_seconds=time.monotonic() - before, http_status=http.last_status)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("trbench-root", "contract", "candidate-key", "vllm-source", "tokenizer", "tokenizer-wheel", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--suite", choices=("gsm8k", "mmlu_pro", "ifeval"), required=True)
    parser.add_argument("--count", type=int, choices=(20, 50, 200), required=True)
    args = parser.parse_args()
    worker.require(sys.flags.isolated and not args.output.exists())
    deadline = time.monotonic() + 3600
    started = datetime.now(timezone.utc).isoformat()
    # All prompts, replies and grader diagnostics remain in process memory.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        tree_sha = source(args.trbench_root, REVISION)
        rows, prompt, grade, dataset_sha = cases(args.suite, args.trbench_root, args.count)
        worker.require(len(rows) == args.count)
        raw = tau_live_identity.private_read(args.contract, deadline)
        worker.require(digest(raw) == args.contract_sha256)
        contract = worker.decode(raw)
        worker.require(contract["schema"] == "nib.tau-external-trial-endpoint/v2"
                       and contract["model"] == MODEL and contract["serving_profile"] == "long1m-graphs"
                       and contract["retire_at"] > time.time() + 3600)
        pem = tau_live_identity.private_read(contract["certificate"], deadline)
        http = bounded_https.HTTPS(contract["hostname"], ("/v1/chat/completions",), address=contract["address"],
            port=contract["port"], certificate_pem=pem, certificate_sha256=contract["certificate_sha256"])
        counter = tau_external_tokenizer.Counter(args.vllm_source, args.tokenizer, args.tokenizer_wheel, context_tokens=32768)
        key = tau_live_identity.private_read(args.candidate_key, deadline).rstrip(b"\n")
        revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], timeout=10).decode().strip()
        for name in ("record-quality-calibration.py", "bounded_https.py", "tau_external_tokenizer.py",
                     "tau_live_identity.py", "tau_trial_worker.py", "tau_selected_policy.py",
                     "engine_tokenizer_parity.py"):
            committed = subprocess.check_output(["git", "-C", str(ROOT), "show", revision + ":scripts/" + name], timeout=10)
            worker.require(committed == (ROOT / "scripts" / name).read_bytes())
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        results = []
        with os.fdopen(fd, "wb") as stream:
            write(stream, {"kind": "manifest", "schema": "nib.quality-calibration/v1", "git_sha": revision,
                "trbench_revision": REVISION, "trbench_tree_sha256": tree_sha, "dataset_sha256": dataset_sha,
                "suite": args.suite, "expected": args.count, "started_at": started,
                "sampling": "proportional-stride" if args.suite == "mmlu_pro" else "first-n",
                "model": MODEL, "temperature": 0, "max_output_tokens": MAX_OUTPUT,
                "reasoning_effort": "none", "concurrency": 1, "retries": 0,
                "endpoint_binding_sha256": args.contract_sha256, "confidential": False,
                "matched_reference": False, "full_quality_qualified": False})
            cancelled = False
            def cancel(signum, frame):
                nonlocal cancelled
                if not cancelled:
                    cancelled = True
                    raise KeyboardInterrupt
            prior = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
            try:
                for index, item in enumerate(rows):
                    if cancelled or time.monotonic() >= deadline:
                        break
                    row = {"kind": "item", "index": index, "status": "cancelled", "stage": "not-started",
                           "usage": None, "score": None, "http_status": None, "generation_started": False,
                           "elapsed_seconds": 0.0}
                    try:
                        results.append(row)
                        evaluate(index, item, prompt, grade, counter, http, key, deadline, row)
                    finally:
                        if any(item is row for item in results):
                            write(stream, row)
                    if row["status"] == "cancelled":
                        break
                    # Stop a broken transport early; retain the full expected denominator.
                    if len(results) >= 3 and all(r["status"] == "protocol_or_transport_error" for r in results[-3:]):
                        break
            finally:
                cancelled = True
                counter.close()
                key = b""
                write(stream, {"kind": "summary", "attempted": len(results), "expected": args.count,
                    "evaluated": sum(row["status"] == "evaluated" for row in results),
                    "errors": sum(row["status"] != "evaluated" for row in results),
                    "missing": args.count - len(results), "ended_at": datetime.now(timezone.utc).isoformat(),
                    "known_prompt_tokens": sum((row["usage"] or {}).get("prompt_tokens", 0) for row in results),
                    "known_completion_tokens": sum((row["usage"] or {}).get("completion_tokens", 0) for row in results),
                    "generation_requests": sum(row["generation_started"] for row in results),
                    "unknown_usage_requests": sum(row["generation_started"] and row["usage"] is None for row in results),
                    "latency_median_seconds": statistics.median(row["elapsed_seconds"] for row in results) if results else None})
                for sig, handler in prior.items():
                    signal.signal(sig, handler)
    print(json.dumps({"suite": args.suite, "attempted": len(results), "expected": args.count,
                      "evaluated": sum(row["status"] == "evaluated" for row in results)}))
    return 0 if len(results) == args.count and all(row["status"] == "evaluated" for row in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        sys.exit("Calibration failed before completion; no response text retained.")
