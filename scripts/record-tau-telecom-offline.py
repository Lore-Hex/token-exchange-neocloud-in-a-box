#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Run original pinned telecom offline with fixed peers, never paid counting."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import signal
import stat
import sys
import tempfile
import time
import uuid

SCRIPT = "scripts/record-tau-telecom-offline.py"
HELPERS = (SCRIPT, "scripts/tau_telecom_offline.py", "scripts/tau_telecom_catalog.py", "scripts/tau_telecom_projection.py")
SECONDS = 960
STAGES = ("input_verification", "provenance", "source_verification", "payload_preparation", "image_verification",
          "container_creation", "container_verification", "trial", "terminal_verification", "cleanup", "completed", "receipt_output")
# Fixture inputs only. Not a statement about available money or spending approval.
BUDGET = {"weekly_cap_usd": "10000", "gpu_committed_usd": "0",
          "cpu_disk_reserved_usd": "0", "other_committed_usd": "0"}


def require(value):
    if not value:
        raise ValueError("offline telecom rejected")


def loader():
    path = Path(__file__).absolute().with_name("tau_trial.py")
    spec = importlib.util.spec_from_file_location("offline_trial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def driver(trial, toolkit, provenance):
    external = trial.parent_driver(toolkit, selected_operational=True, external_simulator=True)
    modules = {"tau_external": external, "tau_runtime": trial.runtime, "tau_external_selection": external.policy.staged()}
    for name in ("tau_telecom_projection", "tau_telecom_catalog", "tau_telecom_offline"):
        path = "scripts/" + name + ".py"
        modules[name] = trial.load(path, provenance["files"][path], modules=modules)
    return modules["tau_telecom_offline"]


def record(args, result, progress):
    deadline = time.monotonic() + SECONDS
    trial = loader()
    progress["stage"] = "provenance"
    provenance = trial.tau.committed_identity(deadline, extra_files=HELPERS)
    toolkit = trial.identity(deadline, True, selected_operational=True, external_simulator=True)
    require(provenance["git_sha"] == toolkit["git_sha"])
    result.update(producer_git_sha=provenance["git_sha"], helper_sha256=provenance["files"],
                  upstream_revision=trial.runtime.REVISION, source_archive_sha256=trial.runtime.ARCHIVE_SHA,
                  source_tree=trial.runtime.SOURCE_TREE, runtime_lock_sha256=trial.runtime.CATALOG_SHA)
    offline = driver(trial, toolkit, provenance)
    progress["stage"] = "source_verification"
    stage = {"plan": trial.selection_plan(args.source_archive, ["telecom"], 1, 42, BUDGET, deadline), "index": args.index}
    contract = offline.catalog.bind(stage, args.source_archive, deadline)
    require(stage["plan"]["producer_git_sha"] == provenance["git_sha"])
    progress["stage"] = "payload_preparation"
    files, plan = trial.prepare(args.source_archive, args.wheelhouse, None, None, None, deadline,
        parent_pipe=True, selected_operational=True, external_simulator=True, full_task=True, stage=stage)
    require(plan["producer"]["git_sha"] == provenance["git_sha"])
    sha = trial.tau.digest(files["trial.json"])
    result.update(plan_sha256=sha, catalog_binding_sha256=trial.tau.digest(trial.tau.encoded(contract)),
                  stage_binding=contract["stage_binding"], selection=plan["selection"])
    require(stat.S_ISSOCK(args.docker_socket.stat().st_mode))
    executable = shutil.which("docker")
    require(executable is not None)
    name = "nib-telecom-offline-" + uuid.uuid4().hex
    held = None
    with tempfile.TemporaryDirectory(prefix="nib-telecom-offline-", dir=Path(tempfile.gettempdir()).resolve()) as directory:
        private = Path(directory) / "docker"
        private.mkdir()
        (private / "config.json").write_text('{"auths":{}}\n')
        prefix = [executable, "--host", "unix://" + str(args.docker_socket), "--config", str(private)]
        env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(private),
               "DOCKER_CONFIG": str(private), "LANG": "C", "LC_ALL": "C"}
        destination = Path(directory) / "inputs"
        trial.publish(destination, files)
        payload = destination / "payload"
        trial.validate_plan(payload, sha, toolkit, deadline, parent_pipe=True, selected_operational=True)
        assertions = offline.external.pipe.selected_assertions(payload, plan["selection"])
        held = offline.Offline(stage, args.source_archive, assertions, deadline)
        def docker(args, limit=65536):
            require(time.monotonic() < deadline - 60)
            return trial.probe.capture([*prefix, *args], timeout=min(30, deadline - 60 - time.monotonic()), limit=limit, env=env)
        created = False
        try:
            progress["stage"] = "image_verification"
            selected = trial.check_image(json.loads(docker([
                "image", "inspect", "--platform", "linux/amd64", trial.runtime.TARGET["image"]], 1 << 20))[0])
            progress.update(stage="container_creation", cleanup="unconfirmed")
            created = True  # Ambiguous create still owns this unique name.
            docker(trial.create_command(name, name, payload, sha, parent_pipe=True, selected_operational=True))
            progress["stage"] = "container_verification"
            def checked():
                info = json.loads(docker(["inspect", name], 1 << 20))[0]
                trial.check_container(info, name, name, payload, sha, selected, parent_pipe=True, selected_operational=True)
                return info
            checked()
            progress["stage"] = "trial"
            result["upstream_execution_attempted"] = True
            diagnostic = offline.external.run_process([*prefix, "start", "--attach", "--interactive", name],
                                                      env, payload, plan, held, deadline)
            result.update(diagnostic=diagnostic, scripted_peers=held.observation(),
                          upstream_executed=diagnostic["trial"].get("upstream_termination") is not None)
            progress["cancelled"] = diagnostic["trial"]["status"] == "cancelled"
            require(held.accepted(diagnostic))
            progress["stage"] = "terminal_verification"
            final = checked()
            require(final["State"]["Running"] is False and final["State"]["ExitCode"] == 0
                    and final["State"]["OOMKilled"] is False)
        except BaseException as exc:
            progress.update(failure_stage=progress["stage"], cancelled=isinstance(exc, KeyboardInterrupt))
            raise
        finally:
            held.close()
            if created:
                progress["stage"] = "cleanup"
                # A separate finite cleanup allowance survives an expired trial.
                trial.probe.capture([*prefix, "rm", "--force", name], timeout=15, env=env)
                progress["cleanup"] = "owned-container-removed"
        progress["stage"] = "cleanup"
    progress["stage"] = "completed"
    result.update(result="pass", completed=True, source_authenticated=True, upstream_executed=True)


def host(args):
    progress = {"stage": "input_verification", "cleanup": "not_created"}
    result = {"schema": "nib.tau-telecom-offline/v1", "result": "fail", "completed": False,
        "source_authenticated": False, "upstream_executed": False, "upstream_execution_attempted": False, "producer_git_sha": None,
        "token_count_parity_verified": False, "live_admission": False, "quality_eligible": False,
        "full_benchmark": False, "confidential": False, "execution_authorized": False,
        "paid_generation_calls": 0, "paid_count_calls": 0, "count_basis": "synthetic-constant-100",
        "budget_basis": "synthetic-fixture-not-available-credit", "cancelled": False,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "command_line": "python3 -I -S -B scripts/record-tau-telecom-offline.py --source-archive PRIVATE "
                        "--wheelhouse PRIVATE --docker-socket PRIVATE --index " + str(args.index) + " --receipt PRIVATE"}
    try:
        require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
        require(all(path.is_absolute() for path in (args.source_archive, args.wheelhouse, args.docker_socket, args.receipt)))
        require(not args.receipt.exists() and type(args.index) is int and 0 <= args.index < 5)
        record(args, result, progress)
    except BaseException as exc:
        result.update(result="fail", completed=False, source_authenticated=False,
                      cancelled=progress.get("cancelled", False) or isinstance(exc, KeyboardInterrupt))
    result.update(ended_utc=datetime.now(timezone.utc).isoformat(),
                  stage=progress.get("failure_stage", progress["stage"]), cleanup=progress["cleanup"])
    if result["stage"] not in STAGES or result["cleanup"] not in ("not_created", "unconfirmed", "owned-container-removed"):
        result.update(result="fail", completed=False, source_authenticated=False, stage="input_verification", cleanup="unconfirmed")
    try:
        require(args.receipt.is_absolute())
        with args.receipt.open("xb") as stream:
            stream.write((json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode())
    except BaseException:
        result.update(result="fail", completed=False, source_authenticated=False, stage="receipt_output")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["result"] == "pass" else 1


def evidence_record(receipt):
    require(receipt["schema"] == "nib.tau-telecom-offline/v1" and receipt["result"] in ("pass", "fail"))
    def stamp(value):
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": receipt["producer_git_sha"],
        "command": receipt["command_line"], "started_utc": stamp(receipt["started_utc"]), "ended_utc": stamp(receipt["ended_utc"]),
        "result": receipt["result"], "execution": "software", "hardware_validated": False,
        "host": {"provider": "local", "machine": "offline-telecom-recorder", "gpus": "none", "driver": None, "cuda": None},
        "notes": "Original pinned telecom worker, scripted peers and synthetic counts. Not quality, parity, live admission or full tau. "
                 "Original receipt.json bytes and any failed receipts are retained."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-archive", "wheelhouse", "docker-socket", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--index", type=int, default=0)
    args = parser.parse_args(argv)
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
    requested = False
    def cancelled(signum, frame):
        nonlocal requested
        if not requested:
            requested = True
            raise KeyboardInterrupt
    for kind in previous:
        signal.signal(kind, cancelled)
    try:
        return host(args)
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)


if __name__ == "__main__":
    sys.exit(main())
