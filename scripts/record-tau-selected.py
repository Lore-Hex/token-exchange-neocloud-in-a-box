#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Explicit selected-checkpoint operational trial on an approved ordinary GPU host."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-selected.py")

import argparse
import datetime
import os
from pathlib import Path
import pwd
import selectors
import signal
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/record-tau-selected.py"
MAX_SECONDS, CLEANUP_SECONDS = 4200, 360


def load(path):
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


runner = load(ROOT / "scripts/tau_trial.py")
cpu_rec = load(ROOT / "scripts/record-tau-cpu-parity.py")
gpu_rec = load(ROOT / "host/record-deepseek-lab.py")
tau, require = runner.tau, runner.require
process_tools = runner.load(cpu_rec.SNAPSHOT, cpu_rec.SNAPSHOT_SHA)


def identity(deadline):
    value = runner.identity(deadline, parent_pipe=True, selected_operational=True)
    names = tuple(dict.fromkeys((SCRIPT, "scripts/test_tau_selected_launch.py", gpu_rec.SCRIPT, gpu_rec.PROBE,
        "scripts/prepare-tau-cpu-parity-native.py", *cpu_rec.SOURCES, *cpu_rec.OWNER_SOURCES)))
    for start in range(0, len(names), 4):
        more = tau.committed_identity(deadline, extra_files=names[start:start + 4])
        require(more["git_sha"] == value["git_sha"])
        value["files"].update(more["files"])
    return value


def environment():
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
            "DOCKER_HOST": "unix:///var/run/docker.sock", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
            "GIT_ALLOW_PROTOCOL": "", "GIT_TERMINAL_PROMPT": "0", "PYTHONDONTWRITEBYTECODE": "1"}


def preflight_images(deadline):
    """Read-only prerequisites; owners still repeat their checks at use time."""
    with tempfile.TemporaryDirectory(prefix="nib-selected-images-") as temporary:
        env = process_tools.controlled_env(Path(temporary) / "control")
        env["DOCKER_HOST"] = environment()["DOCKER_HOST"]

        def inspect(arguments, limit):
            remaining = deadline - time.monotonic()
            require(remaining > 0)
            result = process_tools.execute_process_group(["docker", "image", "inspect", *arguments],
                env=env, timeout=min(15, remaining), stdin=subprocess.DEVNULL,
                max_output_bytes=limit, max_stderr_bytes=1024)
            require(time.monotonic() < deadline)
            return result

        result = inspect(["--platform", runner.runtime.TARGET["platform"], runner.runtime.TARGET["image"]], 65536)
        require(result.returncode == 0 and not result.stderr)
        images = tau.json_value(result.stdout)
        require(type(images) is list and len(images) == 1)
        runner.check_image(images[0])
        # CPU builds use the owner's local context, not the scorer's fixed socket.
        discovery = {key: value for key, value in env.items() if key != "DOCKER_HOST"}
        env = {**discovery, "DOCKER_HOST": process_tools.local_docker_endpoint(
            discovery, Path(pwd.getpwuid(os.getuid()).pw_dir))}
        require(time.monotonic() < deadline)
        process_tools.require_buildkit(env)
        require(time.monotonic() < deadline)
        bases = tau.json_value(runner.runtime.read_file(ROOT / "scripts/e2e/pins.json", 1 << 20, deadline))
        for key in ("go_image", "python_image"):
            cpu_rec.check_base_image(inspect(["--format", "{{.Id}} {{.Os}}/{{.Architecture}}", bases[key]], 1024))


def read_ready(processes, prefixes, deadline):
    """Read only original bounded readiness lines from owned child stdout."""
    buffers, ready = {name: bytearray() for name in processes}, {}
    with selectors.DefaultSelector() as selector:
        for name, process in processes.items():
            selector.register(process.stdout, selectors.EVENT_READ, name)
        while selector.get_map():
            require(time.monotonic() < deadline and all(p.poll() is None for p in processes.values()))
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                data = os.read(key.fd, 4096)
                require(data)
                name, buffer = key.data, buffers[key.data]
                buffer.extend(data)
                require(len(buffer) <= 32768 + len(prefixes[name]) + 1)
                if b"\n" in buffer:
                    line, rest = bytes(buffer).split(b"\n", 1)
                    require(not rest and line.startswith(prefixes[name]))
                    ready[name] = line[len(prefixes[name]):]
                    require(ready[name])
                    selector.unregister(key.fileobj)
    return ready


def admit(ready, toolkit, cpu, gpu, contract, contract_sha, deadline):
    """Join source-authenticated original packets, never reconstruct a READY."""
    require(set(ready) == {"candidate", "support"})
    candidate = gpu.binding(ready["candidate"], contract, contract_sha)
    packet = cpu.llama.worker.decode(ready["support"])
    producer = {"git_sha": toolkit["git_sha"], "script": cpu_rec.SCRIPT,
                "source_sha256": toolkit["files"][cpu_rec.SCRIPT]}
    originals, held = cpu.accept(ready["support"], producer, packet["image_sha256"], deadline)
    support = sys.modules["tau_cpu_owner"].binding(ready["support"])
    policy = runner.selected_policy(toolkit)
    framed = {}
    for role in ("simulator", "evaluator"):
        runtime = cpu.llama.worker.decode(originals[role + "-runtime.json"])
        framed[role] = {"served_model": runtime["model"], "context_tokens": 4096,
            "reasoning_effort": "none", "parallel_tool_calls": False,
            **{key: runtime["serving"][key] for key in ("framing_sha256", "tokenizer_sha256")}}
    # The candidate framing identity binds the exact measured tokenizer parity
    # contract, not the host's guess about a model-specific chat template.
    parity = gpu.worker.decode(ready["candidate"])["parity"]
    framed["candidate"] = {"served_model": policy.configuration()["model_profile"]["served_model_name"],
        "context_tokens": 8192, "reasoning_effort": "none", "parallel_tool_calls": False,
        "tokenizer_sha256": tau.VOCABULARY_DIGEST, "framing_sha256": tau.digest(tau.encoded(parity))}
    frozen = {"schema": policy.TRANSPORT, "input": policy.configuration(),
              "settings": policy.settings(policy.configuration()), "framing": framed,
              "owners": {"candidate": candidate, "support": support}}
    policy.transport_manifest(frozen, tau.digest(tau.encoded(frozen)))
    expiry = {name: min(deadline, time.monotonic() + gpu.worker.decode(raw)["expires_unix"] - time.time(),
                       time.monotonic() + policy.LIMITS["run_seconds"])
              for name, raw in ready.items()}
    require(all(until - time.monotonic() > 60 for until in expiry.values()))
    return frozen, expiry, held, packet["image_sha256"]


def close_and_reap(processes, deadline, *, abort=False):
    result = {}
    # Concurrently drain bounded final CLI output so neither host blocks on a
    # full pipe while its counterpart tears down. Never persist these bytes.
    totals = {name: 0 for name in processes}
    try:
        if abort:
            deadline = min(deadline, time.monotonic() + 180)
            for name, process in processes.items():
                if process.poll() is None:
                    try:
                        # Cancel the GPU outer recorder before READY/stdin relay.
                        # Signal only our child, not its attach/watchdog groups;
                        # its SIGTERM handler owns daemon cleanup and reporting.
                        # The CPU recorder instead unwinds on KeyboardInterrupt.
                        process.send_signal(signal.SIGTERM if name == "candidate" else signal.SIGINT)
                    except ProcessLookupError:
                        pass
        for process in processes.values():
            if not process.stdin.closed:
                process.stdin.close()
        with selectors.DefaultSelector() as selector:
            for name, process in processes.items():
                selector.register(process.stdout, selectors.EVENT_READ, name)
            while selector.get_map():
                require(time.monotonic() < deadline)
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fd, 4096)
                    totals[key.data] += len(block)
                    require(totals[key.data] <= 65536)
                    if not block:
                        selector.unregister(key.fileobj)
            for name, process in processes.items():
                result[name] = process.wait(timeout=max(.001, min(10, deadline - time.monotonic())))
    finally:
        errors = []
        for name, process in processes.items():
            try:
                process_tools.cleanup_process_group(process)
            except BaseException as error:
                errors.append(error)
            finally:
                if not process.stdin.closed:
                    process.stdin.close()
            result.setdefault(name, process.returncode)
        require(not errors)
    return result


def read_pair(directory, deadline):
    documents, hashes = {}, {}
    for name in ("run.json", "output.log"):
        raw = runner.runtime.read_file(directory / name, 1 << 20, deadline)
        documents[name], hashes[name] = tau.json_value(raw), tau.digest(raw)
    return documents, hashes


def final_join(destination, ready, held, cpu_image, toolkit, gpu, contract, gpu_receipt, scorer, exits, deadline):
    """A scorer result cannot substitute for original independent owner reports."""
    cpu_docs, cpu_hashes = read_pair(destination / "cpu", deadline)
    gpu_docs, gpu_hashes = read_pair(destination / "gpu", deadline)
    c, g = cpu_docs["output.log"], gpu_docs["output.log"]
    require(all(docs["run.json"]["git_sha"] == toolkit["git_sha"] for docs in (cpu_docs, gpu_docs)))
    require(c["schema"] == "nib.tau-cpu-owner-run/v1" and c["git_sha"] == toolkit["git_sha"]
            and c["image_id"] == "sha256:" + cpu_image
            and c["sources"] == {name: toolkit["files"][name] for name in (*cpu_rec.SOURCES, *cpu_rec.OWNER_SOURCES)}
            and c["cleanup"] == dict.fromkeys(("container", "image", "build_completion"), "confirmed"))
    runtime = sys.modules["tau_cpu_session"].validate(c["runtime"])
    require(runtime["ready_sha256"] == held["ready_sha256"] and runtime["receipts"] == held["receipts"]
            and runtime["cleanup"] == "confirmed")
    for name, sha in held["receipts"].items():
        require(tau.digest(runner.runtime.read_file(destination / "cpu" / name, 16384, deadline)) == sha)
    require(g["provenance"]["git_sha"] == toolkit["git_sha"]
            and g["provenance"]["receipt_sha256"] == gpu_receipt and g["cleanup"] == "confirmed")
    experiment = g["experiment"]
    # The GPU supervisor already authenticated its contract and final schema;
    # rejoin exact original readiness and the scorer's finish acknowledgement.
    finish = scorer["diagnostic"]["owner_finish"]
    require(type(finish) is dict and set(finish) == {"candidate", "support"})
    gpu.validate_join(ready["candidate"], finish["candidate"], experiment)
    require(runtime["owner"] == finish["support"])
    # A failed trial can still prove cleanup. Join outcomes independently from
    # cleanup, and never turn an acknowledged owner failure into a trial pass.
    passed = scorer["result"] == "pass"
    expected = "pass" if passed else "fail"
    require(scorer["result"] in ("pass", "fail")
            and exits == {"candidate": 1, "support": 0 if passed else 1}
            and c["result"] == cpu_docs["run.json"]["result"] == expected
            and gpu_docs["run.json"]["result"] == "fail" and g["status"] == "BLOCKED"
            and g["deployable"] is False and g["confidential"] is False
            and runtime["result"] == expected and g["functional_pass"] is passed
            and experiment["functional_pass"] is passed and experiment["engine_stopped"] is True
            and scorer["cleanup"] == "confirmed"
            and all((row["status"] == "evaluated") is passed for row in finish.values()))
    probe = gpu_rec.load("selected_final_hardware", ROOT / gpu_rec.PROBE, toolkit["files"][gpu_rec.PROBE])
    hardware = probe.validate_nonconfidential_report(experiment["hardware"], family="B300", expected=8)
    facts = {"gpus": "eight-selected-B300", "driver": hardware["driver"], "cuda": hardware["cuda_driver"]}
    envelope = gpu_docs["run.json"]
    require(envelope["execution"] == "hardware" and envelope["hardware_validated"] is False
            and all(envelope["host"][key] == value for key, value in facts.items()))
    return {"owner_reports": {"cpu": cpu_hashes, "gpu": gpu_hashes}, "result": expected, "host": facts}


def run(args):
    require(sys.platform == "linux" and os.uname().machine == "x86_64")
    cutoff = datetime.datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    remaining = cutoff - time.time()
    require(CLEANUP_SECONDS + 960 < remaining <= MAX_SECONDS)
    end = time.monotonic() + remaining
    work_deadline = end - CLEANUP_SECONDS
    toolkit = identity(time.monotonic() + 120)
    receipt, contract, _, _ = gpu_rec.bundle_inputs(args.gpu_bundle, args.gpu_receipt_sha256)
    require(receipt["git_sha"] == toolkit["git_sha"])
    gpu_rec.validate_runtime_payload(args.gpu_bundle)
    gpu = gpu_rec.owner_module(contract, args.gpu_bundle)
    require(gpu is not None)
    cpu_rec.toolkit(ROOT, selected_owner=True)
    cpu = sys.modules["tau_cpu_session"]
    contract_sha = tau.digest(gpu_rec.read(args.gpu_bundle / "payload/lab.json"))
    preflight_images(min(work_deadline, time.monotonic() + 60))
    files, plan = runner.prepare(args.source_archive, args.wheelhouse, None, None, None,
        min(work_deadline, time.monotonic() + 300), parent_pipe=True, selected_operational=True)
    require(plan["producer"]["git_sha"] == toolkit["git_sha"])
    os.umask(0o077)
    with runner.runtime.directory(args.output.parent) as parent:
        os.mkdir(args.output.name, mode=0o700, dir_fd=parent)
    prepared = args.output / "scorer-payload"
    runner.publish(prepared, files)
    payload = prepared / "payload"
    report = {"schema": "nib.tau-selected-run/v1", "git_sha": toolkit["git_sha"],
        "result": "fail", "stage": "launch", "started_utc": runner.now(), "ended_utc": runner.now(),
        "ready_sha256": {}, "owner_exits": {}, "owner_reports": None, "scorer": None,
        "cleanup": "unconfirmed", "quality_eligible": False, "confidential": False, "live_admission": False}
    processes, ready, held, cpu_image = {}, None, None, None
    host = {"provider": "local", "machine": "approved-selected-operational-lab",
            "gpus": "eight-selected-B300", "driver": None, "cuda": None}
    def cancelled(*_):
        raise KeyboardInterrupt
    old = {number: signal.signal(number, cancelled) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        with cpu.llama.worker.alarm(work_deadline):
            gpu_cutoff = datetime.datetime.fromtimestamp(time.time() + work_deadline - time.monotonic(),
                datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            commands = {"candidate": [sys.executable, "-I", "-S", "-B", str(args.gpu_bundle / "record-deepseek-lab.py"),
                "--run", str(args.gpu_bundle), "--receipt-sha256", args.gpu_receipt_sha256,
                "--weights", str(args.weights), "--output", str(args.output / "gpu"), "--deadline", gpu_cutoff],
                "support": [sys.executable, "-I", "-S", "-B", str(ROOT / cpu_rec.SCRIPT),
                "--archive", str(args.cpu_inputs / "llama.tar.gz"), "--model-06", str(args.cpu_inputs / "0.gguf"),
                "--model-17", str(args.cpu_inputs / "1.gguf"), "--cmake-wheel", str(args.cpu_inputs / cpu_rec.WHEEL),
                "--output", str(args.output / "cpu"), "--selected-owner"]}
            for name, command in commands.items():
                processes[name] = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, env=environment(), bufsize=0, close_fds=True, start_new_session=True)
            report["stage"] = "readiness"
            ready = read_ready(processes, {"candidate": gpu.READY, "support": cpu.READY}, work_deadline)
            frozen, expiry, held, cpu_image = admit(ready, toolkit, cpu, gpu, contract, contract_sha, work_deadline)
            report["ready_sha256"] = {name: tau.digest(raw) for name, raw in ready.items()}
            report["stage"] = "trial"
            handles = {name: {"read_fd": process.stdout.fileno(), "write_fd": process.stdin.fileno(),
                               "deadline": expiry[name]} for name, process in processes.items()}
            report["scorer"] = runner.run(payload, tau.digest(tau.encoded(plan)), args.output / "scorer",
                Path("/var/run/docker.sock"), parent_pipe=True, selected_owners={"manifest": frozen,
                "handles": handles, "deadline": min(work_deadline, *expiry.values())})
    except BaseException:
        report["failure"] = "selected-operational-run-failed"
    finally:
        for number in old:
            signal.signal(number, signal.SIG_IGN)
        try:
            report["owner_exits"] = close_and_reap(processes, end, abort="failure" in report)
            if ready and held and report["scorer"] is not None:
                joined = final_join(args.output, ready, held, cpu_image, toolkit, gpu, contract,
                    args.gpu_receipt_sha256, report["scorer"], report["owner_exits"], time.monotonic() + 30)
                report["owner_reports"] = joined["owner_reports"]
                host.update(joined["host"])
                report["cleanup"] = "confirmed"
                if "failure" not in report and joined["result"] == "pass":
                    report.update(result="pass", stage="completed")
                elif joined["result"] == "fail":
                    report["failure"] = "selected-operational-run-failed"
        except BaseException:
            report["failure"] = "selected-operational-run-failed"
        finally:
            for number, handler in old.items():
                signal.signal(number, handler)
        report["ended_utc"] = runner.now()
        envelope = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T4", "git_sha": toolkit["git_sha"],
            "command": "python3 -I -S -B scripts/record-tau-selected.py --gpu-bundle PRIVATE --gpu-receipt-sha256 DIGEST --weights PRIVATE --cpu-inputs PRIVATE --source-archive PRIVATE --wheelhouse PRIVATE --output PRIVATE --deadline UTC",
            "started_utc": report["started_utc"], "ended_utc": report["ended_utc"], "result": report["result"],
            "execution": "hardware", "hardware_validated": False,
            "host": host,
            "notes": "One fixed operational task, selected checkpoint with small CPU support models. "
                     "GPU lab qualification remains FAIL/BLOCKED even when this operational trial passes. "
                     "Not a full tau benchmark or comparable quality score. No pricing, production admission or confidentiality claim. "
                     "Driver and CUDA facts, when present, are joined from the original GPU owner report; "
                     "CUDA is the driver-supported API version, not a runtime measurement."}
        runner.publish_evidence(args.output / "evidence", {"run.json": tau.encoded(envelope), "output.log": tau.encoded(report)})
    return 0 if report["result"] == "pass" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("gpu-bundle", "weights", "cpu-inputs", "source-archive", "wheelhouse", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--gpu-receipt-sha256", required=True)
    parser.add_argument("--deadline", required=True)
    args = parser.parse_args()
    for name in ("gpu_bundle", "weights", "cpu_inputs", "source_archive", "wheelhouse", "output"):
        path = getattr(args, name)
        require(path.is_absolute() and path == path.resolve())
    require(not os.path.lexists(args.output))
    return run(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as error:
        if isinstance(error, SystemExit) and type(error.code) is int:
            raise
        sys.exit("Selected operational run rejected; no success may be inferred.")
