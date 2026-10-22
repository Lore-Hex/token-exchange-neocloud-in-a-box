#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Native CPU pair readiness/release only; no tau execution or live admission."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-cpu-session.py")

import argparse
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "scripts/record-tau-cpu-session.py"
WORKFLOW = ".github/workflows/tau-cpu-session-native.yml"
SECONDS = 2280
CLEANUP_SECONDS = 60
BOOTSTRAP = Path(__file__).read_bytes()
rec = types.ModuleType("cpu_pair_recorder")
rec.__file__ = str(ROOT / "scripts/record-tau-cpu-parity.py")
exec(compile(Path(rec.__file__).read_bytes(), rec.__file__, "exec", dont_inherit=True), rec.__dict__)
require = rec.require


def evidence_host(verified):
    require(type(verified) is bool)
    return {**rec.host("local"), "provider": "github-actions" if verified else "unverified",
            "machine": "native-cpu-controller" if verified else "unverified"}


def preflight():
    rec.host("github-actions")
    require(os.environ.get("GITHUB_REF") == "refs/heads/main")
    raw = Path("/proc/meminfo").read_bytes()
    require(len(raw) <= 16384)
    rows = dict(line.split(b":", 1) for line in raw.splitlines())
    memory, available = (rows[name].split() for name in (b"MemTotal", b"MemAvailable"))
    require(all(len(value) == 2 and value[1] == b"kB" for value in (memory, available)))
    memory_kib, available_kib = int(memory[0]), int(available[0])
    cpus, free = os.cpu_count(), shutil.disk_usage("/var/lib/docker").free
    require(type(cpus) is int and cpus >= 4 and memory_kib >= 10 * 2**20
            and available_kib >= 8 * 2**20 and free >= 12 * 2**30)
    return {"platform": "linux/amd64", "host_memory_kib": memory_kib,
            "host_available_kib": available_kib, "host_cpus": cpus, "docker_free_bytes": free}


def originals(session, directory, held, producer, deadline):
    identity, tau = session.identity, session.tau
    identity.keys(held, ("schema", "ready_sha256", "expires_unix", "receipts"))
    require(held["schema"] == "nib.tau-cpu-pair-held/v1"
            and type(held["expires_unix"]) is int
            and 5 < held["expires_unix"] - time.time() <= session.cpu.TOTAL_SECONDS)
    tau.sha(held["ready_sha256"])
    identity.keys(held["receipts"], session.FILES)
    documents = {}
    for name in session.FILES:
        tau.sha(held["receipts"][name])
        raw = identity.private_read(directory / name, deadline)
        require(len(raw) <= 16384 and tau.digest(raw) == held["receipts"][name])
        documents[name] = raw.decode("ascii")
    runtime = session.llama.worker.decode(documents["simulator-runtime.json"].encode())
    image = runtime["serving"]["image_sha256"]
    # Reconstruct only the private framing from unchanged freshly published bytes.
    # The producer's readiness digest must bind that exact frame and current source.
    packet = {"schema": "nib.tau-cpu-pair-ready/v1", "producer": producer,
              "image_sha256": image, "expires_unix": held["expires_unix"], "receipts": documents}
    _, checked = session.accept(session.probes.encoded(packet), producer, image, deadline)
    require(checked == held)
    return image


def probe_receipts(session, directory, notice, held, probe, deadline):
    """Bind the fixed second frame to original private scorer files, not text."""
    session.identity.keys(notice, ("schema", "ready_sha256", "plan_sha256", "scorer_sha256",
                                   "result", "cleanup", "diagnostic"))
    require(notice["schema"] == "nib.tau-cpu-probe-held/v1"
            and notice["ready_sha256"] == held["ready_sha256"] and notice["plan_sha256"] == probe["sha"])
    documents = {}
    hashes = {}
    for name in ("run.json", "output.log"):
        raw = session.identity.private_read(directory / "scorer" / name, deadline)
        require(len(raw) <= 65536)
        documents[name], hashes[name] = session.llama.worker.decode(raw), rec.digest(raw)
    row = documents["output.log"]
    session.identity.keys(row, ("schema", "toolkit", "plan_sha256", "cpu_ready_sha256", "image", "platform",
        "started_utc", "ended_utc", "result", "stage", "cleanup", "diagnostic",
        "quality_eligible", "confidential", "live_admission"))
    require(row["schema"] == "nib.tau-cpu-probe-scorer/v1" and row["toolkit"] == probe["toolkit"]
            and row["plan_sha256"] == probe["sha"] and row["cpu_ready_sha256"] == held["ready_sha256"]
            and row["image"] == probe["plan"]["image"] and row["platform"] == probe["plan"]["platform"]
            and row["result"] in ("pass", "fail") and row["stage"] in ("image", "create", "trial", "completed")
            and row["cleanup"] in ("confirmed", "failed", "create-completion-unknown")
            and all(row[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
    for key in ("started_utc", "ended_utc"):
        require(type(row[key]) is str and len(row[key]) == 20 and row[key].isascii()
                and rec.datetime.datetime.strptime(row[key], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == row[key])
    require(row["started_utc"] <= row["ended_utc"])
    if row["diagnostic"] is not None:
        sys.modules["tau_cpu_probe"].validate(row["diagnostic"])
        require(row["diagnostic"]["plan_sha256"] == probe["sha"]
                and row["diagnostic"]["selection"] == probe["plan"]["selection"])
    require(row["result"] != "pass" or row["stage"] == "completed" and row["cleanup"] == "confirmed"
            and row["diagnostic"] is not None and row["diagnostic"]["result"] == "pass")
    require(notice["scorer_sha256"] == rec.digest(session.tau.encoded(row))
            and all(notice[key] == row[key] for key in ("result", "cleanup", "diagnostic"))
            and documents["run.json"] == probe["runner"].probe_evidence_record(row))
    probe["scorer"], probe["receipts"] = row, hashes
    return row


def final_receipts(session, directory, held, image, revision, archive_sha, sources, deadline, *, probe=None):
    result = {}
    for name in ("run.json", "output.log"):
        raw = session.identity.private_read(directory / name, deadline)
        require(len(raw) <= 65536)
        result[name] = (session.llama.worker.decode(raw), rec.digest(raw))
    record, _ = result["run.json"]
    report, _ = result["output.log"]
    session.identity.keys(record, ("schema", "phase", "task", "git_sha", "command", "started_utc",
        "ended_utc", "result", "execution", "hardware_validated", "host", "notes"))
    require(record["schema"] == "nib.evidence/v1" and record["git_sha"] == revision
            and record["phase"] == "P2" and record["task"] == "T3"
            and record["result"] in (("pass", "fail") if probe is not None else ("pass",)) and record["execution"] == "software"
            and record["hardware_validated"] is False
            and record["host"] == rec.host("local")
            and record["command"] == "python3 -I -S -B scripts/record-tau-cpu-parity.py --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE"
                + (" --probe-payload PRIVATE --probe-plan-sha256 " + probe["sha"] if probe is not None else " --session")
            and record["notes"] == ("Fixed original tau CPU support probe with scripted target only. No full tau trial, target quality, live admission or confidential claim."
                if probe is not None else "Fresh held CPU-pair receipts only. No target, tau trial, quality, live admission or confidential claim."))
    for key in ("started_utc", "ended_utc"):
        value = record[key]
        require(type(value) is str and len(value) == 20 and value.isascii()
                and rec.datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == value)
    require(record["started_utc"] <= record["ended_utc"])
    session.identity.keys(report, ("schema", "result", "stage", "git_sha", "source_archive_sha256",
        "runtime", "context_sha256", "sources", "base_images", "image_id", "cleanup",
        "quality_eligible", "confidential", "live_admission") + (("scorer",) if probe is not None else ()))
    require(report["schema"] == ("nib.tau-cpu-probe-run/v1" if probe is not None else "nib.tau-cpu-session-run/v1")
            and report["result"] == record["result"]
            and report["stage"] == ("complete" if record["result"] == "pass" else "runtime") and report["git_sha"] == revision
            and report["source_archive_sha256"] == archive_sha
            and report["sources"] == {name: sources[name] for name in rec.SOURCES}
            and report["image_id"] == "sha256:" + image
            and report["cleanup"] == dict.fromkeys(("container", "image", "build_completion"), "confirmed")
            and all(report[key] is False for key in ("quality_eligible", "confidential", "live_admission")))
    session.tau.sha(report["context_sha256"])
    pins = json.loads((session.cpu.ROOT / "scripts/e2e/pins.json").read_bytes())
    require(report["base_images"] == {key: pins[key] for key in ("go_image", "python_image")})
    runtime = session.validate(report["runtime"])
    require(runtime["ready_sha256"] == held["ready_sha256"] and runtime["receipts"] == held["receipts"]
            and runtime["stage"] == "released" and runtime["acknowledged"] and runtime["cleanup"] == "confirmed")
    if probe is None:
        require(runtime["schema"] == "nib.tau-cpu-session/v2" and runtime["result"] == "pass")
    else:
        require(runtime["schema"] == "nib.tau-cpu-session/v3" and probe["scorer"] == report["scorer"]
                and runtime["probe"] == report["scorer"]["diagnostic"]
                and report["result"] == ("pass" if runtime["result"] == report["scorer"]["result"] == "pass" else "fail"))
        probe_receipts(session, directory, probe["notice"], held, probe, deadline)
    for name, sha in held["receipts"].items():
        require(rec.digest(session.identity.private_read(directory / name, deadline)) == sha)
    return {name: pair[1] for name, pair in result.items()}


def supervise(argv, env, snapshot, session, directory, producer, report, deadline, *, probe=None):
    process = subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
    output, error = bytearray(), bytearray()
    held, image = None, None
    completed, total, released = False, 0, False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, output)
            selector.register(process.stderr, selectors.EVENT_READ, error)
            while selector.get_map() or process.poll() is None:
                require(time.monotonic() < deadline)
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    block = os.read(key.fd, 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(block)
                    total += len(block)
                    require(total <= (21504 if probe is not None else 5120) and len(error) <= 1024
                            and len(output) <= (16384 if probe is not None and held is not None else 4096))
                    if key.data is output and b"\n" in output:
                        require(output.endswith(b"\n") and output.count(b"\n") == 1
                                and process.poll() is None and not released)
                        if held is None:
                            require(output.startswith(session.READY))
                            raw = bytes(output[len(session.READY):-1])
                            held = session.llama.worker.decode(raw)
                            require(session.probes.encoded(held) == raw)
                            report["stage"] = "ready-validation"
                            image = originals(session, directory, held, producer, deadline)
                            report["ready"] = held
                            if probe is not None:
                                report["stage"] = "probe"
                                output.clear()
                                continue
                        else:
                            require(probe is not None and output.startswith(b"NIB-TAU-CPU-PROBE "))
                            raw = bytes(output[len(b"NIB-TAU-CPU-PROBE "):-1])
                            notice = session.llama.worker.decode(raw)
                            require(session.probes.encoded(notice) == raw)
                            probe_receipts(session, directory, notice, held, probe, deadline)
                            probe["notice"] = notice
                            report["probe"] = notice
                        require(process.poll() is None and not error)
                        ack = session.DONE + session.probes.encoded(
                            {"ready_sha256": held["ready_sha256"], "released": True}) + b"\n"
                        session.send(process.stdin.fileno(), ack, min(deadline, time.monotonic() + 5))
                        process.stdin.close()
                        released = True
                        report["stage"] = "release"
                        output.clear()
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            require(code in ((0, 1) if probe is not None else (0,)) and held is not None and released and not output
                    and bytes(error) == b"CPU session: " + (b"pass" if code == 0 else b"fail") + b"; no qualification claim.\n")
            if probe is not None:
                probe["exit_code"] = code
            completed = True
            return held, image
    finally:
        try:
            if not process.stdin.closed:
                process.stdin.close()
        finally:
            try:
                # EOF allows producer cleanup; killing a client is not daemon proof.
                if not completed and process.poll() is None:
                    try:
                        process.wait(timeout=CLEANUP_SECONDS)
                    except subprocess.TimeoutExpired:
                        pass
                snapshot.cleanup_process_group(process)
                report["cleanup"]["process_group"] = "confirmed"
            finally:
                for stream in (process.stdout, process.stderr):
                    stream.close()


def execute(source, env, snapshot, inputs, directory, report, deadline, *, probe=None):
    cpu = rec.toolkit(source)
    session = sys.modules["tau_cpu_session"]
    producer = {"git_sha": report["git_sha"], "script": rec.SCRIPT,
                "source_sha256": report["sources"][rec.SCRIPT]}
    command = [sys.executable, "-I", "-S", "-B", str(source / rec.SCRIPT)]
    for flag, path in zip(("archive", "model-06", "model-17", "cmake-wheel"), inputs):
        command += ["--" + flag, str(path)]
    command += ["--output", str(directory / "pair")]
    if probe is None:
        command += ["--session"]
    else:
        runner = rec.load(source / "scripts/tau_trial.py", "cpu_controller_probe")
        toolkit = runner.identity(min(deadline, time.monotonic() + 60), parent_pipe=True, support_probe=True)
        plan, _ = runner.validate_plan(probe["payload"], probe["sha"], toolkit,
            min(deadline, time.monotonic() + 60), parent_pipe=True, support_probe=True)
        probe.update(runner=runner, toolkit=toolkit, plan=plan)
        command += ["--probe-payload", str(probe["payload"]), "--probe-plan-sha256", probe["sha"]]
    require(cpu.TOTAL_SECONDS == 1800 and time.monotonic() < deadline)
    report["stage"] = "producer"
    report["cleanup"] = {"process_group": "unknown", "producer": "unknown"}
    options = {"probe": probe} if probe is not None else {}
    held, image = supervise(command, env, snapshot, session, directory / "pair", producer, report, deadline, **options)
    report["stage"] = "final-validation"
    report["producer_receipts"] = final_receipts(session, directory / "pair", held, image,
        report["git_sha"], report["source_archive_sha256"], report["sources"], deadline, **options)
    report["cleanup"]["producer"] = "confirmed"
    if probe is not None:
        report["scorer_receipts"] = probe["receipts"]
        require((probe["exit_code"] == 0) == (probe["scorer"]["result"] == "pass"))
        require(probe["scorer"]["result"] == "pass" and probe["scorer"]["cleanup"] == "confirmed")


def evidence_record(revision, probe_sha=None):
    require(rec.re.fullmatch("[0-9a-f]{40}", revision))
    record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": revision,
              "command": "python3 -I -S -B scripts/record-tau-cpu-session.py --archive PRIVATE --model-06 PRIVATE --model-17 PRIVATE --cmake-wheel PRIVATE --output PRIVATE",
              "started_utc": rec.now(), "ended_utc": rec.now(), "result": "fail",
              "execution": "software", "hardware_validated": False, "host": evidence_host(False),
              "notes": "Held CPU pair readiness/release only; no tau, live admission, quality or confidentiality."}
    if probe_sha is not None:
        require(rec.re.fullmatch("[0-9a-f]{64}", probe_sha))
        record["command"] += " --probe-payload PRIVATE --probe-plan-sha256 " + probe_sha
        record["notes"] = "Fixed original tau CPU prompt-fit only; scripted target, no full tau, live admission, quality or confidentiality."
    return record


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("CPU session controller arguments rejected")
    parser = Parser(description=__doc__)
    for name in ("archive", "model-06", "model-17", "cmake-wheel", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--probe-payload", type=Path)
    parser.add_argument("--probe-plan-sha256")
    args = parser.parse_args()
    probing = args.probe_payload is not None or args.probe_plan_sha256 is not None
    require(not probing or args.probe_payload is not None and type(args.probe_plan_sha256) is str
            and rec.re.fullmatch("[0-9a-f]{64}", args.probe_plan_sha256))
    os.umask(0o077)
    deadline = time.monotonic() + SECONDS
    raw = (ROOT / rec.SNAPSHOT).read_bytes()
    require(rec.digest(raw) == rec.SNAPSHOT_SHA)
    snapshot = rec.load(ROOT / rec.SNAPSHOT, "cpu_session_snapshot", raw)
    with tempfile.TemporaryDirectory(prefix="nib-cpu-session-controller-") as temporary:
        work = Path(temporary)
        env = snapshot.controlled_env(work / "control")
        env.update(GIT_ALLOW_PROTOCOL="", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
                   GOPROXY="off", GOSUMDB="off")
        require(not snapshot.git(ROOT, env, "status", "--porcelain", stdout=subprocess.PIPE).stdout)
        source = work / "source"
        revision, files, archive_sha = snapshot.snapshot_commit(ROOT, source, work / "control", env)
        require((source / SCRIPT).read_bytes() == BOOTSTRAP
                and (source / rec.SCRIPT).read_bytes() == rec.BOOTSTRAP
                and rec.digest((source / rec.SNAPSHOT).read_bytes()) == rec.SNAPSHOT_SHA)
        exporter = rec.load(source / "scripts/pilot/export.py", "cpu_session_output")
        directory = args.output.absolute()
        parent = exporter.open_parent(directory)
        try:
            os.mkdir(directory.name, 0o700, dir_fd=parent)
            fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            sources = {name: rec.digest((source / name).read_bytes()) for name in
                       (*rec.SOURCES, SCRIPT, WORKFLOW, "scripts/pilot/export.py", "scripts/e2e/run.py")}
            if probing:
                sources["scripts/prepare-tau-cpu-probe-native.py"] = rec.digest(
                    (source / "scripts/prepare-tau-cpu-probe-native.py").read_bytes())
            report = {"schema": "nib.tau-cpu-controller/v1", "git_sha": revision,
                      "source_archive_sha256": archive_sha, "sources": sources, "result": "fail",
                      "stage": "preflight", "runtime_context": None, "ready": None,
                      "producer_receipts": {}, "cleanup": {"process_group": "not-started", "producer": "not-started"},
                      "live_admission": False, "quality_eligible": False, "confidential": False}
            record = evidence_record(revision, args.probe_plan_sha256 if probing else None)
            if probing:
                report.update(schema="nib.tau-cpu-probe-controller/v1", probe=None, scorer_receipts={})
            rec.write(fd, "run.json", record)
            try:
                report["runtime_context"] = preflight()
                record["host"] = evidence_host(True)
                execute(source, env, snapshot,
                        [p.absolute() for p in (args.archive, args.model_06, args.model_17, args.cmake_wheel)],
                        directory, report, deadline,
                        **({"probe": {"payload": args.probe_payload.absolute(), "sha": args.probe_plan_sha256}} if probing else {}))
                snapshot.validate_snapshot(source, files)
                require(time.monotonic() < deadline)
                report.update(result="pass", stage="complete")
            except BaseException:
                report["result"] = "fail"
            rec.write(fd, "output.log", report)
            record.update(result=report["result"], ended_utc=rec.now())
            rec.write(fd, "run.json.pending", record)
            os.replace("run.json.pending", "run.json", src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            print("CPU pair controller: " + report["result"] + "; no qualification claim.")
            return 0 if report["result"] == "pass" else 1
        finally:
            os.close(fd)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as error:
        if isinstance(error, SystemExit) and type(error.code) is int:
            raise
        sys.exit("CPU pair controller rejected; no successful result may be inferred.")
