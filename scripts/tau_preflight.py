#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline tau2 manifest foundation. No upstream imports or benchmark execution."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/tau_preflight.py")

import argparse
import ast
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import re
import stat
import subprocess
import time

ROOT = Path(__file__).absolute().parent.parent
REVISION = "fc0055dc4e0a316c3f83133267fbd6faaa770992"
PINS = "bench/tau-upstream-pins.json"
PINS_SHA = "4dbc7214f1160bb1b883087fa261d299aedc4b5d77d945c377d32063f518fceb"
DOMAINS = ("airline", "retail", "telecom")
ROLES = ("candidate", "reference", "simulator", "evaluator")
PACK = "models/deepseek-v4.1-flash-1017/1"
WEIGHTS = "1bae0e1088993b9b82de08f59de5be5a34e42170"
VOCABULARY_DIGEST = "c90dfa01249db1be4245780a052ede752e1361c612ac6d08e2bdada7d599476b"
VOCABULARY_CONFIG_DIGEST = "6ac8c8dc065ed118161d02dd532749ae3f52c243deac27872134fae2f50d8547"
MAX_BYTES = 32 << 20
MAX_SECONDS = 120
RETAINED = ("task_ids", "seeds", "settings", "hashes", "numeric_observations")


def require(value):
    if not value:
        raise ValueError("tau preflight rejected")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def json_value(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False))


def regular(path, limit, deadline):
    """Anchored, bounded, no-follow reads; never import the source being inspected."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= limit)
            chunks = []
            remaining = before.st_size
            while remaining:
                require(time.monotonic() < deadline)
                chunk = stream.read(min(remaining, 256 << 10))
                require(chunk)
                chunks.append(chunk)
                remaining -= len(chunk)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns)
            require(not stream.read(1) and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=directory, follow_symlinks=False)))
            return b"".join(chunks)
    finally:
        os.close(directory)


def amount(value):
    require(type(value) is str and re.fullmatch(r"(0|[1-9][0-9]{0,5})(\.[0-9]{1,9})?", value))
    return Decimal(value)


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) and value != "0" * 64)


def integer(value, minimum, maximum):
    require(type(value) is int and minimum <= value <= maximum)


def settings(value):
    require(type(value) is dict and set(value) == {"schema", "stage", "seed", "roles", "serving", "limits"}
            and value["schema"] == "nib.tau-preflight-input/v1" and value["stage"] in ("smoke", "comparison"))
    integer(value["seed"], 0, 2**32 - 1)
    roles = value["roles"]
    require(type(roles) is dict and set(roles) == set(ROLES))
    for role, row in roles.items():
        require(type(row) is dict and set(row) == {"model", "revision", "settings_sha256",
                "input_usd_per_million", "output_usd_per_million", "rate_source_sha256", "generation"})
        for key in ("model", "revision"):
            require(type(row[key]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", row[key])
                    and "://" not in row[key] and ".." not in row[key])
        sha(row["settings_sha256"])
        sha(row["rate_source_sha256"])
        amount(row["input_usd_per_million"])
        amount(row["output_usd_per_million"])
        generation = row["generation"]
        require(type(generation) is dict and set(generation) == {"temperature", "max_tokens", "num_retries"}
                and type(generation["temperature"]) is str and 0 <= amount(generation["temperature"]) <= 2
                and type(generation["num_retries"]) is int and generation["num_retries"] == 0)
        integer(generation["max_tokens"], 1, 8192)
        require(row["settings_sha256"] == digest(encoded(generation)))
        if role in ("candidate", "reference"):
            require(row["model"] == "deepseek-ai/DeepSeek-V4.1-Flash-1017" and row["revision"] == WEIGHTS)
    for role in ("simulator", "evaluator"):
        require(roles[role]["model"] != roles["candidate"]["model"])
    require(roles["candidate"]["generation"] == roles["reference"]["generation"]
            and roles["candidate"]["settings_sha256"] == roles["reference"]["settings_sha256"])
    serving = value["serving"]
    require(type(serving) is dict and set(serving) == {"candidate", "reference"})
    for row in serving.values():
        require(type(row) is dict and set(row) == {"image_sha256", "overlay_sha256", "arguments_sha256",
                "tokenizer_sha256", "tokenizer_config_sha256", "chat_template_sha256", "precision_sha256"})
        for item in row.values():
            sha(item)
        require(row["tokenizer_sha256"] == VOCABULARY_DIGEST
                and row["tokenizer_config_sha256"] == VOCABULARY_CONFIG_DIGEST)
    limits = value["limits"]
    require(type(limits) is dict and set(limits) == {"concurrency", "retries", "turns", "requests",
            "request_seconds", "trial_seconds", "run_seconds", "input_tokens_per_request",
            "output_tokens_per_request", "total_tokens", "cost_usd", "resource_cost_usd"}
            and type(limits["concurrency"]) is int and limits["concurrency"] == 1
            and type(limits["retries"]) is int and limits["retries"] == 0)
    for key, low, high in (("turns", 1, 100), ("requests", 1, 100000), ("request_seconds", 1, 120),
                           ("trial_seconds", 1, 1800), ("run_seconds", 1, 86400),
                           ("input_tokens_per_request", 1, 131072), ("output_tokens_per_request", 1, 8192),
                           ("total_tokens", 1, 100000000)):
        integer(limits[key], low, high)
    require(limits["request_seconds"] <= limits["trial_seconds"] <= limits["run_seconds"]
            and limits["total_tokens"] >= limits["input_tokens_per_request"] + limits["output_tokens_per_request"]
            and 0 < amount(limits["cost_usd"]) <= 100000
            and amount(limits["resource_cost_usd"]) < amount(limits["cost_usd"]))
    require(all(row["generation"]["max_tokens"] <= limits["output_tokens_per_request"] for row in roles.values()))
    return json_value(encoded(value))


def task_ids(raw_tasks, raw_splits):
    tasks, splits = json_value(raw_tasks), json_value(raw_splits)
    if type(tasks) is dict:
        require(set(tasks) == {"tasks"})
        tasks = tasks["tasks"]
    require(type(tasks) is list and 0 < len(tasks) <= 20000
            and type(splits) is dict and type(splits.get("base")) is list)
    ids = []
    for row in tasks:
        require(type(row) is dict and type(row.get("id")) is str
                and re.fullmatch(r"[A-Za-z0-9_:\[\]|-]{1,256}", row["id"]))
        ids.append(row["id"])
    base = splits["base"]
    require(len(ids) == len(set(ids)) and 5 <= len(base) <= len(ids)
            and all(type(item) is str for item in base) and len(base) == len(set(base))
            and set(base) <= set(ids))
    return sorted(base)


def upstream_contract(sources):
    """Compile/inspect only: no upstream imports, functions, prompts or tools run."""
    tree = ast.parse(sources["src/tau2/runner/batch.py"])
    single = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_single_task")
    require([arg.arg for arg in single.args.args] == ["config", "task"])
    required = {"seed", "evaluation_type", "save_dir", "verbose_logs", "audio_debug", "audio_taps", "auto_review"}
    require(required <= {arg.arg for arg in single.args.kwonlyargs})
    config = ast.parse(sources["src/tau2/data_model/simulation.py"])
    classes = {node.name: node for node in config.body if isinstance(node, ast.ClassDef)}
    fields = {node.target.id for name in ("BaseRunConfig", "TextRunConfig") for node in classes[name].body
              if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    require({"domain", "task_split_name", "llm_agent", "llm_args_agent", "llm_user", "llm_args_user",
             "max_steps", "max_errors", "num_trials", "seed", "max_retries", "hallucination_retries",
             "auto_review", "auto_resume", "verbose_logs", "timeout"} <= fields)
    return {"entrypoint": "tau2.runner.batch.run_single_task", "config": "tau2.data_model.simulation.TextRunConfig",
            "evaluation_type": "ALL", "save_dir": None, "verbose_logs": False, "audio_debug": False,
            "audio_taps": False, "auto_review": False, "upstream_imported": False,
            "runtime_adapter": "not-implemented"}


def preflight(source_root, config, pins, toolkit, *, reader=regular, deadline=None):
    config = settings(config)
    require(pins["repository"] == "sierra-research/tau2-bench" and pins["revision"] == REVISION
            and type(pins["files"]) is dict and 1 <= len(pins["files"]) <= 80)
    if deadline is None:
        deadline = time.monotonic() + MAX_SECONDS
    sources, total = {}, 0
    for name, expected in sorted(pins["files"].items()):
        require(type(name) is str and not name.startswith("/") and all(p not in ("", ".", "..") for p in name.split("/"))
                and set(expected) == {"sha256", "git_blob", "bytes"})
        integer(expected["bytes"], 1, 16 << 20)
        raw = reader(Path(source_root) / name, expected["bytes"], deadline)
        total += len(raw)
        require(total <= MAX_BYTES and len(raw) == expected["bytes"] and digest(raw) == expected["sha256"]
                and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected["git_blob"])
        sources[name] = raw
    domains = {}
    for domain in DOMAINS:
        prefix = "data/tau2/domains/" + domain + "/"
        ordered = task_ids(sources[prefix + "tasks.json"], sources[prefix + "split_tasks.json"])
        domains[domain] = {"base_count": len(ordered), "task_ids": ordered[:5] if config["stage"] == "smoke" else ordered}
    trials = 1 if config["stage"] == "smoke" else 4
    rng = random.Random(config["seed"])
    seeds = [rng.randint(0, 1000000) for _ in range(trials)]
    manifest = {"schema": "nib.tau-manifest/v1", "status": "preflight-only", "benchmark_executed": False,
                "scored_run": False, "quality_accepted": False, "confidential": False,
                "toolkit": toolkit, "upstream": pins, "dependency_lock_sha256": digest(sources["uv.lock"]),
                "dependencies_installed_or_verified": False, "inputs_verified_bytes": total,
                "domains": domains, "split": "base", "trial_seeds": seeds, "trials_per_task": trials,
                "task_order": "lexicographic-task-id-before-selection", "role_order": ["candidate", "reference"],
                "expected_trials_per_role": sum(len(row["task_ids"]) for row in domains.values()) * trials,
                "settings": config, "settings_provenance": "operator-declared-not-independently-verified",
                "upstream_contract": upstream_contract(sources),
                "retention": {"raw_trajectories": False, "raw_logs": False, "allowed": list(RETAINED)},
                "blocked_on": ["live-adapter-with-enforced-retention-and-transport-budgets",
                               "qualified-candidate-and-same-revision-reference",
                               "simulator-evaluator-and-resource-cost-approval"]}
    require(time.monotonic() < deadline and len(encoded(manifest)) <= 1 << 20)
    return manifest


class Budget:
    """Future transport boundary, tested with fakes; not upstream cost estimates.

    Reserve worst-case cost/tokens before each request. Failed or unobserved
    usage keeps the reservation and aborts; no implicit zero cost or retry.
    """
    def __init__(self, config, *, clock=time.monotonic):
        self.config, self.clock = settings(config), clock
        self.limits = self.config["limits"]
        self.deadline = clock() + self.limits["run_seconds"]
        self.spent = amount(self.limits["resource_cost_usd"])
        self.tokens = self.requests = 0
        self.pending = None
        self.failed = False
        self.trial_deadline = None
        self.turns = 0

    def begin_trial(self):
        require(not self.failed and self.pending is None and self.trial_deadline is None
                and self.clock() < self.deadline)
        self.trial_deadline = min(self.deadline, self.clock() + self.limits["trial_seconds"])
        self.turns = 0

    def end_trial(self):
        require(not self.failed and self.pending is None and self.trial_deadline is not None
                and self.clock() < self.trial_deadline)
        self.trial_deadline = None

    def reserve(self, role):
        require(not self.failed and self.pending is None and role in ROLES)
        now = self.clock()
        require(self.trial_deadline is not None)
        left = min(self.deadline, self.trial_deadline) - now
        require(left > 0 and self.turns < self.limits["turns"])
        cfg = self.config["roles"][role]
        incoming, outgoing = self.limits["input_tokens_per_request"], cfg["generation"]["max_tokens"]
        price = (amount(cfg["input_usd_per_million"]) * incoming
                 + amount(cfg["output_usd_per_million"]) * outgoing) / 1000000
        require(self.requests < self.limits["requests"] and self.tokens + incoming + outgoing <= self.limits["total_tokens"]
                and self.spent + price <= amount(self.limits["cost_usd"]))
        self.spent += price
        self.tokens += incoming + outgoing
        self.requests += 1
        self.turns += 1
        self.pending = (role, incoming, outgoing, price, min(self.deadline, self.trial_deadline,
                                                           now + self.limits["request_seconds"]))
        return {"timeout_seconds": min(left, self.limits["request_seconds"]), "max_tokens": outgoing, "num_retries": 0}

    def settle(self, usage):
        require(not self.failed and self.pending is not None)
        role, incoming, outgoing, reserved, deadline = self.pending
        # Sticky failure if validation throws or the transport reports no usage.
        self.failed = True
        require(self.clock() < deadline and type(usage) is dict
                and set(usage) == {"prompt_tokens", "completion_tokens", "total_tokens"})
        used_in, used_out = usage["prompt_tokens"], usage["completion_tokens"]
        integer(used_in, 0, incoming)
        integer(used_out, 0, outgoing)
        integer(usage["total_tokens"], 0, incoming + outgoing)
        require(usage["total_tokens"] == used_in + used_out)
        cfg = self.config["roles"][role]
        charged = (amount(cfg["input_usd_per_million"]) * used_in
                   + amount(cfg["output_usd_per_million"]) * used_out) / 1000000
        self.spent += charged - reserved
        self.tokens += used_in + used_out - incoming - outgoing
        self.pending = None
        self.failed = False
        return {"requests": self.requests, "tokens": self.tokens, "cost_usd": format(self.spent, "f")}

    def abandon(self):
        require(self.pending is not None)
        self.failed = True


def committed_identity(deadline, *, extra_files=()):
    """No lazy fetch, replacement objects, hooks, ambient Git or private paths."""
    require(type(extra_files) is tuple and len(extra_files) <= 4
            and all(type(name) is str and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                    and not name.startswith("/") and all(p not in ("", ".", "..") for p in name.split("/"))
                    for name in extra_files))
    env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
           "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    def git(*args):
        require(time.monotonic() < deadline)
        result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(ROOT), *args],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=min(10, max(0.01, deadline - time.monotonic())), check=True)
        require(len(result.stdout) <= 1 << 20)
        return result.stdout
    revision = git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision))
    hashes = {}
    for name in ("scripts/tau_preflight.py", PINS, PACK + "/pack.yaml", PACK + "/files.sha256", *extra_files):
        size = git("cat-file", "-s", revision + ":" + name).decode().strip()
        require(size.isdigit() and 0 < int(size) <= 1 << 20)
        data = git("show", revision + ":" + name)
        require(data == regular(ROOT / name, int(size), deadline))
        hashes[name] = digest(data)
    return {"git_sha": revision, "files": hashes}


def publish(destination, manifest, *, filename="manifest.json"):
    require(filename in ("manifest.json", "summary.json"))
    destination = Path(destination)
    raw = encoded(manifest)
    require(len(raw) <= 1 << 20)
    require(destination.is_absolute() and ".." not in destination.parts)
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in destination.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        # Exclusive creation; never modify, replace or remove existing output.
        os.mkdir(destination.name, mode=0o700, dir_fd=parent)
        directory = os.open(destination.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fsync(parent)
            fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("tau arguments rejected")
    parser = Parser(description=__doc__)
    parser.add_argument("--upstream", required=True, help="Local pinned public input snapshot; never imported.")
    parser.add_argument("--settings", required=True, help="Public settings only; no endpoints or credentials.")
    parser.add_argument("--output", required=True, help="New private directory; no overwrite.")
    args = parser.parse_args()
    deadline = time.monotonic() + MAX_SECONDS
    pins_raw = regular(ROOT / PINS, 1 << 20, deadline)
    require(digest(pins_raw) == PINS_SHA)
    config = json_value(regular(Path(args.settings), 65536, deadline))
    manifest = preflight(Path(args.upstream), config, json_value(pins_raw), committed_identity(deadline), deadline=deadline)
    require(time.monotonic() < deadline)
    publish(Path(args.output), manifest)
    print(json.dumps({"preflight": "pass", "benchmark_executed": False,
                      "manifest_sha256": digest(encoded(manifest))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Tau preflight rejected; no benchmark was executed.")
