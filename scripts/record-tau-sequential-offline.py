#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise the sequential controller with the original worker and fixed peers."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-sequential-offline.py")

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import signal
import tempfile
import time
import types
from unittest import mock

SCRIPT = "scripts/record-tau-sequential-offline.py"
HELPERS = (SCRIPT, "scripts/record-tau-telecom-offline.py", "scripts/tau_telecom_offline.py")
STAGES = ("provenance", "plan", "controller", "verification", "completed")


def load():
    path = Path(__file__).absolute().with_name("tau_trial.py")
    spec = importlib.util.spec_from_file_location("offline_sequential_trial", path)
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    return trial.load("scripts/record-tau-sequential.py")


def replay(args, progress):
    entry = load()
    trial, deadline = entry.trial, time.monotonic() + 3600
    trial.require(all(path.is_absolute() for path in (args.source_archive, args.wheelhouse, args.docker_socket,
                                                     args.output, args.receipt))
                  and not args.output.exists() and not args.receipt.exists())
    progress["stage"] = "provenance"
    provenance = trial.tau.committed_identity(deadline, extra_files=HELPERS)
    toolkit = trial.identity(deadline, True, selected_operational=True, external_simulator=True)
    trial.require(toolkit["git_sha"] == provenance["git_sha"])
    progress.update(producer_git_sha=provenance["git_sha"], helper_sha256=provenance["files"])
    recorder = trial.load(HELPERS[1], provenance["files"][HELPERS[1]])
    # The production factory stays closed. Only this explicitly offline recorder
    # substitutes fixed peers; all credential reads and HTTPS constructors fail.
    original_driver = trial.parent_driver
    peers = []
    binding = trial.tau.digest(b"sequential offline fixture; no endpoint or available-credit claim")
    def forbidden(*args, **kwargs):
        raise ValueError("offline network or credential access rejected")
    with ExitStack() as stack:
        def driver(toolkit, **kwargs):
            external = original_driver(toolkit, **kwargs)
            if kwargs.get("external_simulator"):
                offline = recorder.driver(trial, toolkit, {"files": {
                    **toolkit["files"], **provenance["files"]}})
                def runtime(*arguments, full_task, stage, support_source):
                    trial.require(full_task is True and support_source == args.source_archive
                                  and arguments[:7] == (Path("/offline-unused"), binding, *([Path("/offline-unused")] * 5)))
                    held = offline.Offline(stage, support_source, [], arguments[-1], source_bound=True)
                    held.manifest["binding_sha256"] = binding
                    held.policy = offline.policy
                    held.policy.transport_manifest(held.manifest, trial.tau.digest(trial.tau.encoded(held.manifest)))
                    peers.append(held)
                    return held
                stack.enter_context(mock.patch.object(external, "Runtime", side_effect=runtime))
                stack.enter_context(mock.patch.object(external.private, "private_read", side_effect=forbidden))
                stack.enter_context(mock.patch.object(external.bounded_https, "HTTPS", side_effect=forbidden))
                stack.enter_context(mock.patch.object(external.tau_external_tokenizer, "Counter", side_effect=forbidden))
            return external
        # recorder.driver loads its own verified closure, not this wrapper.
        offline = recorder.driver(trial, toolkit, {"files": {**toolkit["files"], **provenance["files"]}})
        progress["stage"] = "plan"
        value = trial.selection_plan(args.source_archive, ["telecom"], 3, 42, recorder.BUDGET, deadline,
                                     aggregate_cap_usd="70.044864")
        trial.require(len(value["selections"]) == 15 and all(
            offline.catalog.selection.assertion_count({"plan": value, "index": index}) == 0 for index in range(15)))
        # Reuse the verified fixed peers without recursively loading the wrapper.
        stack.enter_context(mock.patch.object(recorder, "driver", return_value=offline))
        stack.enter_context(mock.patch.object(trial, "parent_driver", side_effect=driver))
        with tempfile.TemporaryDirectory(prefix="nib-sequential-offline-") as temporary:
            plan_path = Path(temporary).resolve() / "selection.json"
            with plan_path.open("xb") as stream:
                stream.write(trial.tau.encoded(value))
            parameters = types.SimpleNamespace(selection_plan=plan_path,
                selection_plan_sha256=trial.tau.digest(trial.tau.encoded(value)), source_archive=args.source_archive,
                wheelhouse=args.wheelhouse, docker_socket=args.docker_socket, contract_sha256=binding, output=args.output,
                deadline_utc=datetime.fromtimestamp(time.time() + 3500, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                **{name: Path("/offline-unused") for name in ("contract", "candidate_key", "simulator_key",
                                                           "vllm_source", "tokenizer", "tokenizer_wheel")})
            progress["stage"] = "controller"
            result = entry.execute(parameters, {"stage": "input-verification", "cancelled": False})
        progress["stage"] = "verification"
        raw = trial.runtime.read_file(args.output / "trial-00/output.log", 1 << 20, deadline)
        record = trial.tau.json_value(raw)
        trial.require(len(peers) == 1 and peers[0].accepted(record["trial"])
                      and result["result"] == "incomplete" and result["stop_reason"] == "cap-exhausted"
                      and result["planned_trials"] == 15 and result["completed_trials"] == 1
                      and result["held_trial_usd"] == "0" and record["cleanup"] == "confirmed"
                      and trial.runtime.read_file(args.output / "receipt-00.json", 1 << 20, deadline) == raw
                      and not (args.output / "trial-01").exists())
        progress.update(stage="completed", result="pass", completed=True, source_authenticated=True,
            controller=result, original_receipt_sha256=trial.tau.digest(raw), scripted_peers=peers[0].observation(),
            original_task_reward=record["trial"]["trial"]["reward"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-archive", "wheelhouse", "docker-socket", "output", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    entry = load()
    result = {"schema": "nib.tau-sequential-offline/v1", "result": "fail", "completed": False,
        "stage": "provenance", "source_authenticated": False, "producer_git_sha": None,
        "paid_generation_calls": 0, "paid_count_calls": 0, "token_count_parity_verified": False,
        "count_basis": "synthetic-constant-100", "budget_basis": "synthetic-fixture-not-available-credit",
        "quality_eligible": False, "live_admission": False, "confidential": False, "full_benchmark": False,
        "command_line": "python3 -I -S -B " + SCRIPT + " --source-archive PRIVATE --wheelhouse PRIVATE "
                        "--docker-socket PRIVATE --output PRIVATE --receipt PRIVATE",
        "started_utc": entry.trial.now(), "cancelled": False}
    def cancel(signum, frame):
        if not result["cancelled"]:
            result["cancelled"] = True
            raise KeyboardInterrupt
    prior = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        for sig in prior:
            signal.signal(sig, cancel)
        replay(args, result)
    except BaseException as exc:
        result.update(result="fail", completed=False, source_authenticated=False,
                      cancelled=result["cancelled"] or isinstance(exc, KeyboardInterrupt))
    finally:
        for sig, handler in prior.items():
            signal.signal(sig, handler)
    result["ended_utc"] = entry.trial.now()
    try:
        entry.trial.require(result["stage"] in STAGES and args.receipt.is_absolute())
        with args.receipt.open("xb") as stream:
            stream.write(entry.trial.tau.encoded(result))
    except BaseException:
        return 1
    print(entry.trial.tau.encoded(result).decode(), end="")
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
