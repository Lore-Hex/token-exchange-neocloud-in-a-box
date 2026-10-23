#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Offline original-policy tokenizer lower bound; no model generation or endpoint."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/record-tau-agent-prefix.py")

import hashlib
import os
from pathlib import Path
import signal
import stat
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/record-tau-agent-prefix.py"


def load_entry():
    path = ROOT / "scripts/record-engine-cpu-entry.py"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= 1 << 20:
            raise ValueError("source rejected")
        raw = stream.read((1 << 20) + 1)
    module = types.ModuleType("prefix_cpu_owner")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module, raw


entry, ENTRY_SOURCE = load_entry()
require = entry.overlay.require


def tokenizer_inputs(directory, parity):
    files = {}
    for name, (size, expected) in parity.MODEL_FILES.items():
        raw = entry.read_source(directory / name, size)
        require(len(raw) == size and hashlib.sha256(raw).hexdigest() == expected)
        files[name] = raw
    return files


def execute(archive, wheelhouse, tokenizer_directory, socket, destination, *, scorer_image_profile=None):
    revision, bundle, extra = entry.inputs(agent_prefix=True)
    require(extra[SCRIPT] == entry.read_source(Path(__file__).absolute())
            and extra[entry.SCRIPT] == ENTRY_SOURCE
            and socket.is_absolute() and stat.S_ISSOCK(socket.stat().st_mode))
    trial, trial_source = entry.load("prefix_scorer_owner", ROOT / "scripts/tau_trial.py")
    profile = trial.PREFIX_IMAGE_PROFILES[0] if scorer_image_profile is None else scorer_image_profile
    require(type(profile) is str and profile in trial.PREFIX_IMAGE_PROFILES)
    parity, _ = entry.load("prefix_parity", ROOT / "scripts/engine_tokenizer_parity.py")
    models = tokenizer_inputs(tokenizer_directory, parity)
    started = trial.now()
    result = {"schema": "nib.tau-agent-prefix-record/v1", "git_sha": revision,
        "started_utc": started, "ended_utc": None, "stage": "scorer-inputs", "result": "fail",
        "scorer_image_profile": profile,
        "render": None, "tokenizer": None, "observation": None,
        "quality_eligible": False, "confidential": False, "full_fit_proven": False,
        "generation_performed": False}
    # Reserve before either daemon sees a create request. An unfinished marker
    # is not evidence of cleanup or a count. Final files are exclusive as well.
    entry.recorder.prep.publish(destination, {"pending.json": entry.overlay.json_bytes({
        "schema": "nib.tau-agent-prefix-pending/v1", "git_sha": revision, "started_utc": started,
        "result": "incomplete", "cleanup": "unproven"})})
    signal.signal(signal.SIGTERM, entry.recorder.cancel)
    signal.signal(signal.SIGINT, entry.recorder.cancel)
    try:
        with tempfile.TemporaryDirectory(prefix="nib-agent-prefix-", dir="/private/tmp" if sys.platform == "darwin" else None) as temporary:
            work = Path(temporary).resolve()
            files, plan = trial.prepare(archive, wheelhouse, None, None, None, time.monotonic() + 180,
                                       parent_pipe=True, support_probe=True, agent_prefix=True)
            require(plan["producer"]["git_sha"] == revision and files[trial.SCRIPT] == trial_source)
            trial.publish(work / "scorer", files)
            scorer = work / "scorer/payload"
            result["stage"] = "scorer-render"
            body, rendered = trial.run(scorer, trial.tau.digest(files["trial.json"]), destination / "render", socket,
                                       parent_pipe=True, agent_prefix=True, scorer_image_profile=profile)
            result["render"] = rendered
            require(not entry.recorder.CANCELLED)
            result["stage"] = "tokenizer-inputs"
            payload, contract = entry.prepare_files(revision, bundle, extra, agent_prefix=body, tokenizer_files=models)
            require(contract["sources"]["agent-prefix.json"] == rendered["prefix"]["request_sha256"])
            directory = work / "tokenizer"
            entry.publish_container_payload(directory, payload)
            config = entry.recorder.raw_metadata(ROOT / "bench/engine-cpu-entry/oci", contract)
            env = entry.recorder.offline_environment(work / "control")
            env["DOCKER_HOST"] = "unix://" + str(socket)
            result["stage"] = "tokenizer-execution"
            observed = entry.run_container(directory, contract, config, env)
            observed["provenance"] = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
                "payload_sha256": entry.overlay.bundle_digest(payload), "requested_image": contract["base_image"],
                "expected_config_sha256": contract["official_config"]["sha256"],
                "raw_oci_metadata_verified": True, "measurement_sha256": contract["measurement_sha256"],
                "render_plan_sha256": rendered["plan_sha256"], "request_sha256": rendered["prefix"]["request_sha256"],
                "model_files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                                for name, raw in models.items()}}
            result["tokenizer"] = observed
            require(observed["stage"] == "completed" and observed["entry_pass"] is True
                    and observed["cleanup"] == "confirmed" and "failure" not in observed)
            result["observation"] = observed["experiment"]["entry"]["agent_prefix"]
        result.update(stage="completed", result="pass")
    except BaseException:
        # Fixed stage only; upstream errors, tracebacks, prompts and IDs never
        # enter the receipt. A partial private render receipt remains original.
        pass
    result["ended_utc"] = trial.now()
    record = trial.evidence_record(revision, "0" * 64, started, result, parent_pipe=True)
    record.update(command="python3 -I -S -B " + SCRIPT + " --source-archive PRIVATE --wheelhouse PRIVATE "
        "--tokenizer-directory PRIVATE --docker-socket PRIVATE --output PRIVATE --scorer-image-profile " + profile,
        notes="Original static retail task 2 policy/tools in sealed scorer, then exact pinned CPU tokenizer. "
              "A completed count is not full request fit, tau execution, model quality or confidential proof. "
              "No weights, generation, endpoint or GPU. Private request body is never an artifact.")
    require(len(trial.tau.encoded(result)) <= 65536)
    trial.publish_evidence(destination / "result", {"run.json": trial.tau.encoded(record), "output.log": trial.tau.encoded(result)})
    return 0 if result["result"] == "pass" else 1


def main():
    parser = entry.overlay.QuietParser(description=__doc__)
    for name in ("source-archive", "wheelhouse", "tokenizer-directory", "docker-socket", "output"):
        parser.add_argument("--" + name, required=True, type=lambda raw: Path(raw).absolute())
    parser.add_argument("--scorer-image-profile")
    args = parser.parse_args()
    return execute(args.source_archive, args.wheelhouse, args.tokenizer_directory, args.docker_socket, args.output,
                   scorer_image_profile=args.scorer_image_profile)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Agent prefix preflight rejected; no fit may be inferred.")
