#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Serial, content-free TR benchmark calibration against one pinned TLS node."""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import importlib.abc
import importlib.util
import json
import math
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


def candidate_transport(contract, deadline):
    """Select TLS only from the hash-bound contract, never by retry/detection."""
    worker.require(type(contract) is dict and contract.get("model") == MODEL
                   and contract.get("serving_profile") == "long1m-graphs")
    cutoff = contract.get("retire_at")
    now = time.time()
    worker.require(type(cutoff) in (int, float) and math.isfinite(cutoff)
                   and cutoff > now + 3600)
    certificate = {}
    if contract.get("schema") == "nib.tau-external-trial-endpoint/v2":
        worker.require("tls_mode" not in contract)
        mode = "pinned"
        certificate = {"certificate_pem": tau_live_identity.private_read(contract["certificate"], deadline),
                       "certificate_sha256": contract["certificate_sha256"]}
    else:
        worker.require(contract.get("schema") == "nib.quality-calibration-endpoint/v1"
                       and set(contract) == {"schema", "model", "serving_profile", "serving_context_tokens",
                                             "retire_at", "tls_mode", "hostname", "address", "port"}
                       and contract["tls_mode"] == "system-roots"
                       and type(contract["serving_context_tokens"]) is int
                       and contract["serving_context_tokens"] == 1048576
                       and type(contract["retire_at"]) is int and cutoff <= now + 7 * 86400
                       and type(contract["port"]) is int and contract["port"] == 443
                       and type(contract["address"]) is str)
        mode = "system-roots"
    http = bounded_https.HTTPS(contract["hostname"], ("/v1/chat/completions",),
                              address=contract["address"], port=contract["port"], **certificate)
    return http, mode


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source(root, revision, modules=None):
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], timeout=10, stderr=subprocess.DEVNULL).decode().strip()
    worker.require(head == revision)
    names = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-r", "--name-only", revision,
                                     "--", "trbench", "uv.lock"], timeout=10, stderr=subprocess.DEVNULL).decode().splitlines()
    worker.require({"uv.lock", "trbench/__init__.py", "trbench/evals/gsm8k/score.py",
                    "trbench/evals/mmlu_pro/score.py", "trbench/evals/ifeval/vendor/evaluation_lib.py"} <= set(names))
    hashes = {}
    for name in names:
        path = root / name
        committed = subprocess.check_output(["git", "-C", str(root), "show", revision + ":" + name], timeout=10, stderr=subprocess.DEVNULL)
        worker.require(path.is_file() and not path.is_symlink() and path.read_bytes() == committed)
        hashes[name] = digest(committed)
        if modules is not None and name.startswith("trbench/") and name.endswith(".py"):
            modules[name] = committed
    return digest(worker.encoded(hashes))


class VerifiedSources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile only verified bytes, never cached bytecode or untracked modules."""
    def __init__(self, root, modules):
        self.root, self.modules = root, modules

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "trbench" and not fullname.startswith("trbench."):
            return None
        name = fullname.replace(".", "/")
        for relative, package in ((name + "/__init__.py", True), (name + ".py", False)):
            if relative in self.modules:
                spec = importlib.util.spec_from_loader(fullname, self, origin=str(self.root / relative), is_package=package)
                spec.loader_state = relative
                return spec
        raise ImportError("Unverified benchmark module")

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        relative = module.__spec__.loader_state
        module.__file__ = str(self.root / relative)
        exec(compile(self.modules[relative], module.__file__, "exec"), module.__dict__)


@contextlib.contextmanager
def verified_imports(root, modules):
    names = lambda: [name for name in sys.modules if name == "trbench" or name.startswith("trbench.")]
    worker.require(not names())
    finder = VerifiedSources(root, modules)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        for name in names():
            del sys.modules[name]


def cases(suite, root, count):
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
    worker.require(type(usage) is dict)
    incoming, outgoing = usage.get("prompt_tokens"), usage.get("completion_tokens")
    worker.require(type(incoming) is int and type(outgoing) is int and 0 <= incoming < 2**31
                   and 0 <= outgoing < 2**31 and type(usage.get("total_tokens")) is int
                   and usage["total_tokens"] == incoming + outgoing)
    return {"prompt_tokens": incoming, "completion_tokens": outgoing}


def measured_cached_tokens(raw, prompt_tokens):
    details = raw["usage"].get("prompt_tokens_details")
    if details is None:
        return None
    worker.require(type(details) is dict)
    cached = details.get("cached_tokens")
    worker.require(cached is None or type(cached) is int and 0 <= cached <= prompt_tokens)
    return cached


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


class RecordingFailure(BaseException):
    """A receipt failure stops the run, never another inference attempt."""


class Journal:
    def __init__(self, stream):
        self.stream = stream
        self.failed = False

    def write(self, row):
        if self.failed:
            raise RecordingFailure("Calibration receipt unavailable")
        try:
            raw = worker.encoded(row)
            worker.require(self.stream.write(raw) == len(raw))
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except BaseException:
            self.failed = True
            raise RecordingFailure("Calibration receipt unavailable") from None


@contextlib.contextmanager
def journal(path):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        # Persist the newly created directory entry before any request can send.
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        yield Journal(stream)


def evaluate(index, item, prompt, grade, counter, http, key, deadline, row=None, before_send=lambda: None):
    if row is None:
        row = {}
    row.update(kind="item", index=index, status="infrastructure_error", stage="render",
               usage=None, cached_prompt_tokens=None, score=None, http_status=None, generation_started=False)
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
            def record_send():
                before_send()
                row["generation_started"] = True
            reply = http.post("/v1/chat/completions", body, key, min(deadline, before + 119),
                              before_send=record_send)
            row["stage"] = "response-validation"
            row["usage"] = measured_usage(reply)
            # Keep measured totals even when optional cache accounting is malformed.
            row["cached_prompt_tokens"] = measured_cached_tokens(reply, row["usage"]["prompt_tokens"])
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
    class PrivateParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("Invalid calibration arguments")
    parser = PrivateParser(description=__doc__)
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
    with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        modules = {}
        tree_sha = source(args.trbench_root, REVISION, modules)
        stack.enter_context(verified_imports(args.trbench_root, modules))
        rows, prompt, grade, dataset_sha = cases(args.suite, args.trbench_root, args.count)
        worker.require(len(rows) == args.count)
        raw = tau_live_identity.private_read(args.contract, deadline)
        worker.require(digest(raw) == args.contract_sha256)
        contract = worker.decode(raw)
        http, tls_mode = candidate_transport(contract, deadline)
        counter = tau_external_tokenizer.Counter(args.vllm_source, args.tokenizer, args.tokenizer_wheel, context_tokens=32768)
        key = tau_live_identity.private_read(args.candidate_key, deadline).rstrip(b"\n")
        revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], timeout=10, stderr=subprocess.DEVNULL).decode().strip()
        for name in ("record-quality-calibration.py", "bounded_https.py", "tau_external_tokenizer.py",
                     "tau_live_identity.py", "tau_trial_worker.py", "tau_selected_policy.py",
                     "engine_tokenizer_parity.py"):
            committed = subprocess.check_output(["git", "-C", str(ROOT), "show", revision + ":scripts/" + name], timeout=10, stderr=subprocess.DEVNULL)
            worker.require(committed == (ROOT / "scripts" / name).read_bytes())
        results = []
        with journal(args.output) as stream:
            stream.write({"kind": "manifest", "schema": "nib.quality-calibration/v3", "git_sha": revision,
                "trbench_revision": REVISION, "trbench_tree_sha256": tree_sha, "dataset_sha256": dataset_sha,
                "suite": args.suite, "expected": args.count, "started_at": started,
                "sampling": "proportional-stride" if args.suite == "mmlu_pro" else "first-n",
                "model": MODEL, "temperature": 0, "max_output_tokens": MAX_OUTPUT,
                "reasoning_effort": "none", "concurrency": 1, "retries": 0,
                "endpoint_binding_sha256": args.contract_sha256, "confidential": False,
                "candidate_tls_mode": tls_mode,
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
                           "usage": None, "cached_prompt_tokens": None, "score": None,
                           "http_status": None, "generation_started": False,
                           "elapsed_seconds": 0.0}
                    try:
                        results.append(row)
                        evaluate(index, item, prompt, grade, counter, http, key, deadline, row,
                                 before_send=lambda: stream.write({"kind": "intent", "index": index}))
                    finally:
                        if not stream.failed and any(item is row for item in results):
                            stream.write(row)
                    if row["status"] == "cancelled":
                        break
                    # Stop a broken transport early; retain the full expected denominator.
                    if len(results) >= 3 and all(r["status"] == "protocol_or_transport_error" for r in results[-3:]):
                        break
            finally:
                cancelled = True
                try:
                    counter.close()
                    key = b""
                    if not stream.failed:
                        stream.write({"kind": "summary", "attempted": len(results), "expected": args.count,
                            "evaluated": sum(row["status"] == "evaluated" for row in results),
                            "errors": sum(row["status"] != "evaluated" for row in results),
                            "missing": args.count - len(results), "ended_at": datetime.now(timezone.utc).isoformat(),
                            "known_prompt_tokens": sum((row["usage"] or {}).get("prompt_tokens", 0) for row in results),
                            "known_completion_tokens": sum((row["usage"] or {}).get("completion_tokens", 0) for row in results),
                            "known_cached_prompt_tokens": sum(row["cached_prompt_tokens"] or 0 for row in results),
                            "generation_requests": sum(row["generation_started"] for row in results),
                            "unknown_usage_requests": sum(row["generation_started"] and row["usage"] is None for row in results),
                            "unknown_cache_usage_requests": sum(row["generation_started"] and row["cached_prompt_tokens"] is None for row in results),
                            "latency_median_seconds": statistics.median(row["elapsed_seconds"] for row in results) if results else None})
                finally:
                    for sig, handler in prior.items():
                        signal.signal(sig, handler)
    print(json.dumps({"suite": args.suite, "attempted": len(results), "expected": args.count,
                      "evaluated": sum(row["status"] == "evaluated" for row in results)}))
    return 0 if len(results) == args.count and all(row["status"] == "evaluated" for row in results) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, RecordingFailure):
        sys.exit("Calibration failed before completion; no response text retained.")
