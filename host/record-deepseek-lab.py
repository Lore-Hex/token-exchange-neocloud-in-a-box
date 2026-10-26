#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare offline, or explicitly run one private nonqualifying CC experiment."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S host/record-deepseek-lab.py")

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import tempfile
import time
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "host/record-deepseek-lab.py"
BUILDER = "scripts/prepare-engine-application.py"
BUILDER_SHA = "68147e708cef42d70bf7634581891b04dd67c3311d2a718d31955f66dc80931e"
HELPER = "scripts/engine_python_bootstrap.py"
RUNTIME = "scripts/engine_lab.py"
LAB_BOOTSTRAP = "scripts/engine_lab_bootstrap.py"
EXCEPTIONS = "scripts/engine_exception.py"
PROBE = "scripts/engine_lab_probe.py"
CORRECTNESS = "scripts/engine_local_correctness.py"
PARITY = "scripts/engine_tokenizer_parity.py"
OBSERVATION = "scripts/engine_runtime_observation.py"
PROVIDER = "scripts/provider_conformance_session.py"
PROVIDER_WORKER = "scripts/provider_conformance_worker.py"
STREAM = "scripts/stream_lab.py"
TAU_SOURCES = "scripts/tau_gpu_sources.py"
SMOKE = "scripts/engine_import_smoke.py"
TEST = "scripts/test_deepseek_lab.py"
SIX_TEST = "scripts/test_botocore_six.py"
PACK = "models/deepseek-v4.1-flash-1017/1"
MODEL_PROFILE = "scripts/deepseek_model_profile.py"
TUNING = "scripts/deepseek_tuning.py"
MODEL_PROFILE_SHA = "11475b07991fb510704d64143fcd9a0084327e586699604f14f88436960b754a"
SUPPORT = ("scripts/record-engine-image.py", "scripts/prepare-engine-overlay.py",
           "scripts/check-engine-candidate.py", "scripts/engine_overlay.py",
           "scripts/engine_image_diagnostic.py", "scripts/engine_python_descriptor.py", "scripts/e2e/snapshot.py")
MAX_BYTES = 64 << 20
MAX_SECONDS = 4800
ENGINE_MEMORY_GIB = 512
WATCHDOG_SETTLE_SECONDS = 40
LABEL = "nib.deepseek-lab"
CANCELLED = False
CLEANING = False


class Cancelled(BaseException):
    """Cancellation must escape selector EINTR handling into owned cleanup."""


def require(value):
    if not value:
        raise ValueError("lab contract rejected")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, limit=2 << 20):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        raw = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(raw, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= limit)
            data = stream.read(limit + 1)
            def identity(s):
                return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(len(data) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                    and identity(before) == identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)))
            return data
    finally:
        os.close(fd)


def load(name, path, expected=None):
    raw = read(path)
    require(expected is None or digest(raw) == expected)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", flags=0, dont_inherit=True), module.__dict__)
    return module


def committed(builder, revision, name):
    row = builder.checker.git(ROOT, "ls-tree", revision, "--", name).decode().split()
    require(len(row) == 4 and row[:2] == ["100644", "blob"] and row[3] == name)
    require(int(builder.checker.git(ROOT, "cat-file", "-s", row[2])) <= 2 << 20)
    return builder.checker.git(ROOT, "cat-file", "blob", row[2])


def correctness_target(family, security, replicas, model_profile):
    return security == "confidential" and (family == "B300" or
            (family == "B200" and type(replicas) is int and replicas == 2 and model_profile == "uncensored"))


def ordinary_uncensored_target(family, security, replicas, model_profile):
    return (family == "B300" and security == "nonconfidential" and type(replicas) is int
            and replicas == 2 and model_profile == "uncensored")


def model_target(family, security, replicas, model_profile):
    return (correctness_target(family, security, replicas, model_profile)
            or ordinary_uncensored_target(family, security, replicas, model_profile))


def tuning_measurement_options(parity, materialization, observation, cohort, profile, provider, execution_profile=None):
    options = (parity, materialization, observation, cohort)
    require(provider is None and all(type(value) is bool for value in options))
    if execution_profile in ("tau8k-eager", "quality32k-eager", "long128k-eager", "long128k-graphs"):
        require(parity and materialization and observation and not cohort and profile is None)
    else:
        require(all(options) or not any(options) and profile is None)


def tuning_module(contract, path):
    if "execution_profile" not in contract:
        require("tuning.py" not in contract.get("sources", {}))
        return None
    tuning = load("lab_tuning_profile", Path(path) / "payload/tuning.py", contract["sources"]["tuning.py"])
    tuning.enabled(contract)
    return tuning


def owner_module(contract, path):
    if "tau_owner" not in contract:
        require("tau-owner-sources.py" not in contract.get("sources", {}))
        return None
    path = Path(path) / "payload"
    loader = load("lab_tau_sources", path / "tau-owner-sources.py", contract["sources"]["tau-owner-sources.py"])
    sources = {loader.PREFIX + name + ".py": read(path / (loader.PREFIX + name + ".py")) for name in loader.NAMES}
    module = loader.load(sources, contract["sources"])
    module.selected(contract)
    return module


def provider_target(family, security, replicas, model_profile):
    return (family == "B300" and type(replicas) is int and replicas == 2
            and ((security == "confidential" and model_profile in (None, "original"))
                 or (security == "nonconfidential" and model_profile == "uncensored")))


def parallel_size(contract):
    size = contract.get("data_parallel_size", 1)
    require(type(size) is int and size in (1, 2))
    require(size == 1 or (contract.get("operation") == "local-correctness"
            and model_target(contract.get("gpu_family"), contract.get("gpu_security", "confidential"), size,
                contract["model_profile"].get("name") if type(contract.get("model_profile")) is dict else None)))
    return size


def selected_model(raw, name):
    require(digest(raw) == MODEL_PROFILE_SHA)
    module = types.ModuleType("lab_model_profiles")
    exec(compile(raw, MODEL_PROFILE, "exec", dont_inherit=True), module.__dict__)
    return module, module.select(name)


def validate_model_profile(contract, path):
    if "model_profile" not in contract:
        require("model-profile.py" not in contract.get("sources", {}))
        return None
    raw = read(Path(path) / "payload/model-profile.py")
    require(digest(raw) == contract["sources"].get("model-profile.py"))
    module, _ = selected_model(raw, "uncensored")
    identity = module.validate_contract(contract)
    require(contract["operation"] == "local-correctness" and parallel_size(contract) == 2
            and model_target(contract.get("gpu_family"), contract.get("gpu_security", "confidential"),
                                   2, identity["name"]))
    if "provider_conformance" in contract:
        require(provider_target(contract.get("gpu_family"), contract.get("gpu_security", "confidential"),
                                2, identity["name"]))
        require("provider-session.py" in contract["sources"])
        load("selected_provider_session", Path(path) / "payload/provider-session.py",
             contract["sources"]["provider-session.py"]).enabled(contract)
    module.validate_manifest("uncensored", read(Path(path) / "payload/pack/files.sha256"))
    arguments = contract["arguments"]
    require(arguments.count("--served-model-name") == 1
            and arguments[arguments.index("--served-model-name") + 1] == identity["served_model_name"])
    return identity


def make_payload(builder, revision, bundle, extra, binary, operation="functional", family="B200", security="confidential", replicas=1,
                 *, tokenizer_parity=False, tokenizer_materialization=False, runtime_observation=False,
                 provider_conformance=None, stream_cohort=False, stream_profile=None, model_profile="original",
                 execution_profile=None, tau_owner=False):
    require(type(model_profile) is str and model_profile in ("original", "uncensored"))
    identity = None
    pack = PACK
    if model_profile != "original":
        require(operation == "local-correctness" and model_target(family, security, replicas, model_profile)
                and type(replicas) is int and replicas == 2)
        profiles, identity = selected_model(extra[MODEL_PROFILE], model_profile)
        pack = identity["pack"]
        profiles.validate_manifest(model_profile, extra[pack + "/files.sha256"])
    require(operation in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    require(family in ("B200", "B300") and (family == "B200" or operation in ("local-correctness", "pre-serve")))
    require(security in ("confidential", "nonconfidential") and (security == "confidential"
            or operation == "local-correctness"))
    parallel_size({"operation": operation, "gpu_family": family, "gpu_security": security,
                   "data_parallel_size": replicas, **({"model_profile": identity} if identity else {})})
    require(type(tokenizer_parity) is bool and (not tokenizer_parity or
            (operation == "local-correctness" and model_target(family, security, replicas, model_profile))))
    require(type(tokenizer_materialization) is bool and (not tokenizer_materialization or tokenizer_parity))
    require(type(runtime_observation) is bool and (not runtime_observation or
            (operation == "local-correctness" and model_target(family, security, replicas, model_profile))))
    require(type(stream_cohort) is bool and (not stream_cohort or
            runtime_observation and tokenizer_parity and tokenizer_materialization and provider_conformance is None))
    require(stream_profile is None or stream_cohort and type(stream_profile) is str)
    files, _ = builder.prepare_files(revision, bundle, extra)
    app = builder.overlay.load_json(files["payload/contract.json"])
    measured = builder.application.measurement(builder.overlay, extra[builder.application.EVIDENCE])
    runtime = types.ModuleType("lab_constants")
    exec(compile(extra[RUNTIME], RUNTIME, "exec", dont_inherit=True), runtime.__dict__)
    plan = builder.recorder.prep.launch_plan()
    prefix = b'import sys\nif not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):\n    sys.exit("Isolated lab required.")\n'
    payload = {"application/" + name.removeprefix("payload/"): data for name, data in files.items() if name.startswith("payload/")}
    payload.update({"launch.py": prefix + extra[HELPER] + b"\n" + extra[EXCEPTIONS] + b"\n" + extra[LAB_BOOTSTRAP] + b"\n" + extra[RUNTIME],
                    "exception.py": extra[EXCEPTIONS],
                    "probe.py": extra[PROBE], "launch-plan.json": builder.overlay.json_bytes(plan),
                    "processes.py": extra[builder.recorder.SNAPSHOT],
                    "nib": binary, "pack/pack.yaml": extra[pack + "/pack.yaml"],
                    "pack/files.sha256": extra[pack + "/files.sha256"]})
    if identity is not None:
        payload["model-profile.py"] = extra[MODEL_PROFILE]
    if operation == "local-correctness":
        payload["correctness.py"] = extra[CORRECTNESS]
    if tokenizer_parity:
        payload["tokenizer-parity.py"] = extra[PARITY]
    if tokenizer_materialization:
        payload["tokenizer-adapter.py"] = extra[LAB_BOOTSTRAP]
    if runtime_observation:
        payload["runtime-observation.py"] = extra[OBSERVATION]
    if stream_cohort:
        stream = types.ModuleType("lab_stream_constants")
        exec(compile(extra[STREAM], STREAM, "exec", dont_inherit=True), stream.__dict__)
        payload["stream-lab.py"] = extra[STREAM]
        for name in stream.source_names(stream_profile):
            payload["stream/" + name + ".py"] = extra["scripts/" + name + ".py"]
    require(type(tau_owner) is bool)
    if tau_owner:
        loader = types.ModuleType("lab_tau_source_names")
        exec(compile(extra[TAU_SOURCES], TAU_SOURCES, "exec", dont_inherit=True), loader.__dict__)
        payload["tau-owner-sources.py"] = extra[TAU_SOURCES]
        for name in loader.NAMES:
            payload[loader.PREFIX + name + ".py"] = extra["scripts/" + name + ".py"]
    if provider_conformance is not None:
        require(type(provider_conformance) is dict and runtime_observation and tokenizer_materialization
                and tokenizer_parity and provider_target(family, security, replicas, model_profile)
                and not stream_cohort and execution_profile is None)
        payload["provider-session.py"] = extra[PROVIDER]
        payload["provider-worker.py"] = extra[PROVIDER_WORKER]
    contract = {"schema": "nib.deepseek-lab-input/v1", "status": "BLOCKED", "deployable": False, "confidential": False,
                "operation": operation,
                "git_sha": revision, "base_image": builder.overlay.BASE_IMAGE, "official_config": app["official_config"],
                "source_bundle_sha256": builder.checker.bundle_digest({**bundle, **extra}),
                "application_contract_sha256": digest(payload["application/contract.json"]),
                "candidate_bundle_sha256": app["candidate_bundle_sha256"],
                "validation_source_sha256": digest(extra[SMOKE]),
                "pack_revision": identity["hf_revision"] if identity else runtime.MODEL_REVISION,
                "pack_bytes": identity["total_bytes"] if identity else runtime.TOTAL_BYTES,
                "manifest_sha256": digest(payload["pack/files.sha256"]),
                "interpreter": {key: measured["files"][0][key] for key in ("resolved", "bytes", "sha256")},
                "paths": measured["proposed_paths"],
                "arguments": runtime.lab_args(plan, replicas, **({"model": identity["served_model_name"]} if identity else {})),
                "environment": runtime.lab_environment(plan), "sources": {name: digest(data) for name, data in payload.items()}}
    if identity is not None:
        contract["model_profile"] = identity
    if family == "B300" or identity is not None:
        contract["gpu_family"] = family
    if security == "nonconfidential":
        contract["gpu_security"] = security
    if replicas == 2:
        contract["data_parallel_size"] = replicas
    if tokenizer_parity:
        contract["tokenizer_parity"] = True
    if tokenizer_materialization:
        adapter = types.ModuleType("lab_adapter_constants")
        exec(compile(extra[LAB_BOOTSTRAP], LAB_BOOTSTRAP, "exec", dont_inherit=True), adapter.__dict__)
        contract["tokenizer_materialization"] = adapter.tokenizer_adapter_identity()
    if runtime_observation:
        contract["runtime_observation"] = True
    if stream_cohort:
        contract["stream_cohort"] = {"schema": stream.SCHEMA}
        if stream_profile is not None:
            contract["stream_cohort"]["profile"] = stream_profile
    if provider_conformance is not None:
        contract["provider_conformance"] = provider_conformance
        session = types.ModuleType("lab_provider_contract")
        exec(compile(extra[PROVIDER], PROVIDER, "exec", dont_inherit=True), session.__dict__)
        session.enabled(contract)
    if execution_profile is not None:
        tuning = types.ModuleType("lab_tuning")
        exec(compile(extra[TUNING], TUNING, "exec", dont_inherit=True), tuning.__dict__)
        payload["tuning.py"] = extra[TUNING]
        contract["sources"]["tuning.py"] = digest(extra[TUNING])
        contract["execution_profile"] = execution_profile
        tuning.enabled(contract)
        contract["arguments"] = tuning.arguments(contract["arguments"], execution_profile)
    if tau_owner:
        module = loader.load(payload, contract["sources"])
        contract["tau_owner"] = {"schema": module.SCHEMA}
        module.selected(contract)
    if stream_cohort:
        stream.enabled(contract)
    payload["lab.json"] = builder.overlay.json_bytes(contract)
    payload["python"] = runtime.wrapper_bytes(contract["interpreter"]["resolved"], contract["environment"], digest(payload["lab.json"]))
    return payload, contract


def prepare(destination, operation="functional", family="B200", security="confidential", replicas=1, *,
            tokenizer_parity=False, tokenizer_materialization=False, runtime_observation=False,
            provider_wheelhouse=None, stream_cohort=False, stream_profile=None, model_profile="original",
            execution_profile=None, provider_image_profile=None, tau_owner=False):
    require(stream_profile is None or stream_cohort)
    require(provider_image_profile is None or (provider_image_profile == "docker28-classic"
            and provider_wheelhouse is not None))
    require(type(model_profile) is str and model_profile in ("original", "uncensored"))
    require(model_profile == "original" or (operation == "local-correctness"
            and model_target(family, security, replicas, model_profile)
            and type(replicas) is int and replicas == 2))
    require(not (family == "B200" and replicas == 2) or model_profile == "uncensored")
    builder = load("lab_application_builder", ROOT / BUILDER, BUILDER_SHA)
    revision, bundle, extra = builder.inputs(ROOT)
    pack = PACK
    if model_profile != "original":
        extra[MODEL_PROFILE] = committed(builder, revision, MODEL_PROFILE)
        _, identity = selected_model(extra[MODEL_PROFILE], model_profile)
        pack = identity["pack"]
        name = "scripts/test_deepseek_model_profile.py"
        extra[name] = committed(builder, revision, name)
    for name in (SCRIPT, HELPER, RUNTIME, LAB_BOOTSTRAP, EXCEPTIONS, PROBE, SMOKE, TEST, SIX_TEST,
                 pack + "/pack.yaml", pack + "/files.sha256"):
        extra[name] = committed(builder, revision, name)
    if operation == "local-correctness":
        for name in (CORRECTNESS, "scripts/test_engine_local_correctness.py"):
            extra[name] = committed(builder, revision, name)
    if execution_profile is not None:
        require(operation == "local-correctness" and ordinary_uncensored_target(family, security, replicas, model_profile)
                and provider_wheelhouse is None)
        tuning_measurement_options(tokenizer_parity, tokenizer_materialization, runtime_observation,
                                   stream_cohort, stream_profile, provider_wheelhouse, execution_profile)
        for name in (TUNING, "scripts/test_deepseek_tuning.py"):
            extra[name] = committed(builder, revision, name)
        load("lab_tuning_selection", ROOT / TUNING, digest(extra[TUNING])).select(execution_profile)
    if tokenizer_parity:
        for name in (PARITY, "scripts/test_engine_tokenizer_parity.py"):
            extra[name] = committed(builder, revision, name)
    if tokenizer_materialization:
        name = "scripts/test_tokenizer_materialization.py"
        extra[name] = committed(builder, revision, name)
    if runtime_observation:
        for name in (OBSERVATION, "scripts/test_engine_runtime_observation.py"):
            extra[name] = committed(builder, revision, name)
    if stream_cohort:
        extra[STREAM] = committed(builder, revision, STREAM)
        stream = load("lab_stream_sources", ROOT / STREAM, digest(extra[STREAM]))
        names = stream.source_names(stream_profile)
        for name in ("scripts/" + item + ".py" for item in (*names, "test_stream_lab")):
            extra[name] = committed(builder, revision, name)
    require(type(tau_owner) is bool)
    if tau_owner:
        require(execution_profile == "tau8k-eager" and not stream_cohort and provider_wheelhouse is None
                and tokenizer_parity and tokenizer_materialization and runtime_observation
                and ordinary_uncensored_target(family, security, replicas, model_profile))
        extra[TAU_SOURCES] = committed(builder, revision, TAU_SOURCES)
        loader = load("lab_tau_source_names", ROOT / TAU_SOURCES, digest(extra[TAU_SOURCES]))
        for name in (*loader.NAMES, "test_tau_gpu_owner", "test_tau_gpu_lab"):
            source = "scripts/" + name + ".py"
            extra[source] = committed(builder, revision, source)
    checker_files = provider_settings = provider_prep = None
    if provider_wheelhouse is not None:
        require(operation == "local-correctness" and provider_target(family, security, replicas, model_profile)
                and runtime_observation and tokenizer_parity and tokenizer_materialization
                and not stream_cohort and execution_profile is None)
        for name in (PROVIDER, PROVIDER_WORKER, "scripts/provider_conformance.py",
                     "scripts/tau_runtime.py", "scripts/tau_trial_worker.py", "scripts/tau_preflight.py",
                     "scripts/e2e/requirements.lock", "scripts/test_provider_conformance_session.py"):
            extra[name] = committed(builder, revision, name)
        provider_prep = load("lab_provider_preparation", ROOT / "scripts/provider_conformance.py",
                             digest(extra["scripts/provider_conformance.py"]))
        require(provider_prep.identity(time.monotonic() + 60)["git_sha"] == revision)
        checker_files = provider_prep.prepare(Path(provider_wheelhouse), time.monotonic() + 60,
                                             model_profile=model_profile)
        require(checker_files["worker.py"] == extra[PROVIDER_WORKER])
        session = load("lab_provider_settings", ROOT / PROVIDER, digest(extra[PROVIDER]))
        provider_settings = {"schema": "nib.owned-provider-input/v1", "image": session.IMAGE,
                             "payload_sha256": digest(checker_files["trial.json"])}
        if provider_image_profile is not None:
            provider_settings["image_profile"] = provider_image_profile
    require(extra[SCRIPT] == read(Path(__file__).absolute()))
    recorder = builder.recorder
    with tempfile.TemporaryDirectory(prefix="nib-lab-build-") as temporary:
        work = Path(temporary).resolve()
        env = recorder.offline_environment(work / "control")
        binary, source_sha = recorder.build_validator(ROOT, revision, work, {**env, "GOOS": "linux", "GOARCH": "amd64"})
        payload, contract = make_payload(builder, revision, bundle, extra, read(binary, MAX_BYTES), operation, family, security, replicas,
                                         tokenizer_parity=tokenizer_parity, tokenizer_materialization=tokenizer_materialization,
                                         runtime_observation=runtime_observation, provider_conformance=provider_settings,
                                         stream_cohort=stream_cohort, stream_profile=stream_profile, model_profile=model_profile,
                                         execution_profile=execution_profile, tau_owner=tau_owner)
        config = recorder.raw_metadata(ROOT / "build/engine-image-metadata", contract)
        runtime = load("lab_environment_constants", ROOT / RUNTIME, digest(extra[RUNTIME]))
        image_env = dict(item.split("=", 1) for item in config["config"]["Env"])
        require(image_env["LD_LIBRARY_PATH"] == runtime.IMAGE_LD_PATH and image_env["CUDA_HOME"] == "/usr/local/cuda")
        files = {"record-deepseek-lab.py": extra[SCRIPT], **{"payload/" + n: b for n, b in payload.items()}}
        files["support/" + SMOKE] = extra[SMOKE]
        for name in SUPPORT:
            files["support/" + name] = (bundle if name in bundle else extra)[name]
        for name in ("manifest.json", "config.json"):
            files["metadata/" + name] = read(ROOT / "build/engine-image-metadata" / name)
        receipt = {"schema": "nib.deepseek-lab-bundle/v1", "git_sha": revision,
                   "status": "BLOCKED", "deployable": False, "confidential": False,
                   "platform": "linux/amd64", "validator_source_sha256": source_sha,
                   "payload_sha256": builder.checker.bundle_digest(payload),
                   "source_bundle_sha256": contract["source_bundle_sha256"],
                   "files": {n: {"bytes": len(b), "sha256": digest(b)} for n, b in files.items()}}
        files["receipt.json"] = recorder.overlay.json_bytes(receipt)
        recorder.prep.publish(destination, files)
        if checker_files is not None:
            provider_prep.publish(Path(destination) / "checker", checker_files)
        for name in ("payload/nib", "payload/python"):
            (Path(destination) / name).chmod(0o500)
    print(json.dumps({"git_sha": revision, "receipt_sha256": digest(files["receipt.json"]),
                      "status": "BLOCKED", "executed": False}, sort_keys=True))


def bundle_inputs(path, expected):
    path = Path(path)
    require(path.is_absolute() and re.fullmatch(r"[0-9a-f]{64}", expected))
    raw = read(path / "receipt.json")
    require(digest(raw) == expected)
    receipt = json.loads(raw)
    require(receipt["schema"] == "nib.deepseek-lab-bundle/v1" and receipt["status"] == "BLOCKED"
            and receipt["deployable"] is False and receipt["confidential"] is False
            and receipt["platform"] == "linux/amd64" and re.fullmatch(r"[0-9a-f]{40}", receipt["git_sha"]))
    require(type(receipt["files"]) is dict and len(receipt["files"]) <= 130)
    total = 0
    for name, row in receipt["files"].items():
        require(re.fullmatch(r"[A-Za-z0-9_./-]+", name) and not name.startswith("/")
                and all(p not in ("", ".", "..") for p in name.split("/")))
        data = read(path / name, MAX_BYTES if name == "payload/nib" else 2 << 20)
        total += len(data)
        require(row == {"bytes": len(data), "sha256": digest(data)} and total <= 80 << 20)
    actual = set()
    directories_seen = 0
    provider_present = "payload/provider-session.py" in receipt["files"]
    for parent, directories, names in os.walk(path, followlinks=False):
        if Path(parent) == path and provider_present:
            require("checker" in directories and not (path / "checker").is_symlink())
            directories.remove("checker")
        directories_seen += 1
        require(directories_seen <= 130 and len(Path(parent).relative_to(path).parts) <= 16
                and len(directories) <= 130)
        require(len(actual) + len(names) <= 150)
        for name in directories:
            require(not (Path(parent) / name).is_symlink())
        actual.update((Path(parent) / name).relative_to(path).as_posix() for name in names)
    require(actual == set(receipt["files"]) | {"receipt.json"})
    require(read(Path(__file__).absolute()) == read(path / "record-deepseek-lab.py"))
    # This is the already reviewed raw-OCI/config/platform/DiffID verifier and
    # bounded process implementation, not a second image verification engine.
    recorder = load("lab_record_support", path / "support/scripts/record-engine-image.py",
                    receipt["files"]["support/scripts/record-engine-image.py"]["sha256"])
    contract = recorder.overlay.load_json(read(path / "payload/lab.json"))
    require(contract["git_sha"] == receipt["git_sha"] and contract["status"] == "BLOCKED"
            and contract["deployable"] is False and contract["confidential"] is False
            and contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    family = contract.get("gpu_family", "B200")
    require(family in ("B200", "B300") and (family == "B200" or contract["operation"] in ("local-correctness", "pre-serve")))
    probe = load("lab_profile_validation", path / "payload/probe.py", contract["sources"]["probe.py"])
    probe.security_profile(contract)
    require(probe.parallel_size(contract) == parallel_size(contract))
    validate_model_profile(contract, path)
    tuning_module(contract, path)
    owner_module(contract, path)
    if "tokenizer_parity" in contract:
        checker = load("lab_parity_profile", path / "payload/tokenizer-parity.py", contract["sources"]["tokenizer-parity.py"])
        require(checker.enabled(contract))
    if "tokenizer_materialization" in contract:
        adapter = load("lab_adapter_profile", path / "payload/tokenizer-adapter.py", contract["sources"]["tokenizer-adapter.py"])
        require(adapter.tokenizer_adapter_options(contract, require))
    if "runtime_observation" in contract:
        observer = load("lab_runtime_profile", path / "payload/runtime-observation.py",
                        contract["sources"]["runtime-observation.py"])
        require(observer.enabled(contract))
    if "stream_cohort" in contract:
        stream = load("lab_stream_profile", path / "payload/stream-lab.py", contract["sources"]["stream-lab.py"])
        stream.enabled(contract)
    require(provider_present == ("provider_conformance" in contract))
    if provider_present:
        session = load("lab_provider_session", path / "payload/provider-session.py",
                       contract["sources"]["provider-session.py"])
        settings = session.enabled(contract)
        worker = load("lab_provider_worker", path / "payload/provider-worker.py",
                      contract["sources"]["provider-worker.py"])
        checker = path / "checker"
        require(stat.S_ISDIR(checker.lstat().st_mode)
                and sorted(p.name for p in checker.iterdir()) == ["payload"])
        plan = worker.verify(checker / "payload", settings["payload_sha256"])
        require(plan["files"]["worker.py"]["sha256"] == contract["sources"]["provider-worker.py"])
        session.bind_model(plan, contract)
    config = recorder.raw_metadata(path / "metadata", contract)
    return receipt, contract, config, recorder


def memory_gib(contract, bundle):
    replicas = parallel_size(contract)
    if "model_profile" in contract:
        # Only the source-bound uncensored DP2 identity has this cap.
        validate_model_profile(contract, bundle)
        return ENGINE_MEMORY_GIB
    return 8 if contract["operation"] == "pre-serve" else ENGINE_MEMORY_GIB * replicas


def validate_runtime_payload(bundle):
    """UID 0 with all capabilities dropped cannot read another UID's 0600 files."""
    root = Path(bundle) / "payload"
    pending, seen = [root], 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        seen += 1
        require(seen <= 260 and len(path.relative_to(root).parts) <= 16
                and info.st_uid == 0 and not info.st_mode & 0o022)
        if stat.S_ISDIR(info.st_mode):
            require(info.st_mode & 0o500 == 0o500)
            pending.extend(path.iterdir())
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and info.st_mode & 0o400 == 0o400)
            if path in (root / "python", root / "nib"):
                require(info.st_mode & 0o100 == 0o100)


def create_command(name, owner, bundle, weights, contract, config):
    require(contract["operation"] in ("functional", "hardware-diagnostic", "local-correctness", "pre-serve"))
    payload = str(Path(bundle) / "payload")
    pre_serve = contract["operation"] == "pre-serve"
    replicas = parallel_size(contract)
    devices = ",".join(str(i) for i in range(4 * replicas))
    memory = str(memory_gib(contract, bundle)) + "g"
    require((weights is None) == pre_serve)
    command = ["docker", "container", "create", "--name", name, "--label", LABEL + "=" + owner,
               "--pull", "never", "--platform", "linux/amd64", "--runtime", "nvidia",
               "--gpus", '"device=' + devices + '"', "--network", "none", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--pids-limit", "1024",
               "--memory", memory, "--memory-swap", memory,
               "--cpus", "2" if pre_serve else "64",
               "--ipc", "private", "--shm-size", "2g", "--cgroupns", "private",
               "--restart", "no", "--log-driver", "none", "--no-healthcheck", "--user", "0:0",
               "--workdir", "/", "--tmpfs", "/cache:rw,nosuid,nodev,exec,size=16g,mode=0700",
               "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
               "--mount", "type=bind,src=" + payload + ",dst=/opt/nib,readonly",
               *([] if pre_serve else ["--mount", "type=bind,src=" + str(weights) + ",dst=/model,readonly"]),
               "--entrypoint", "/usr/bin/env"]
    if "provider_conformance" in contract or "tau_owner" in contract:
        command.insert(3, "--interactive")
    names = set()
    constraints = {}
    for item in config["config"].get("Env", []):
        key, sep, value = item.partition("=")
        require(sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key) and key not in names)
        names.add(key)
        if key.startswith("NVIDIA_REQUIRE_"):
            constraints[key] = value
    # Toolkit device injection is selected at the Docker boundary. Application
    # environment starts from env -i; host credentials/proxies never propagate.
    # Retain the verified image's vendor constraints for the NVIDIA prestart
    # hook; env -i runs only after that hook has enforced them.
    for key in sorted(names | {"PATH", "HOME", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"}):
        command += ["--env", key + "=" + ({**constraints, "NVIDIA_VISIBLE_DEVICES": devices,
                                          "NVIDIA_DRIVER_CAPABILITIES": "compute,utility"}.get(key, ""))]
    return command + [contract["base_image"], "-i", *[key + "=" + value for key, value in sorted(contract["environment"].items())],
                      "/usr/bin/python3.12", "-I", "-S", "-B", "/opt/nib/launch.py",
                      digest(read(Path(bundle) / "payload/lab.json")),
                      "diagnose-hardware" if contract["operation"] == "hardware-diagnostic" else "apply"]


def check_container(info, name, owner, bundle, weights, contract, config, image_id):
    pre_serve = contract["operation"] == "pre-serve"
    command = create_command(name, owner, bundle, weights, contract, config)
    cfg, host = info["Config"], info["HostConfig"]
    require(info["Image"] == image_id and cfg["Image"] == contract["base_image"]
            and cfg["Labels"].get(LABEL) == owner and cfg["Entrypoint"] == ["/usr/bin/env"]
            and cfg["Cmd"] == command[command.index(contract["base_image"]) + 1:]
            and cfg["User"] == "0:0" and cfg["WorkingDir"] == "/" and not cfg.get("Tty")
            and bool(cfg.get("OpenStdin")) == ("provider_conformance" in contract or "tau_owner" in contract))
    env = [command[i + 1] for i, arg in enumerate(command) if arg == "--env"]
    require(len(cfg["Env"]) == len(env) and set(cfg["Env"]) == set(env))
    require(host["NetworkMode"] == "none" and not host["Privileged"] and host["Runtime"] == "nvidia"
            and host["ReadonlyRootfs"] is False
            and host["CapDrop"] == ["ALL"] and not host.get("CapAdd") and not host.get("Devices")
            and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
            and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom") and not host.get("Binds")
            and host["IpcMode"] == "private" and host["ShmSize"] == 2 << 30 and host["CgroupnsMode"] == "private"
            and host["Memory"] == host["MemorySwap"] == memory_gib(contract, bundle) << 30
            and host["NanoCpus"] == (2 if pre_serve else 64) * 1000000000
            and host["PidsLimit"] == 1024 and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
            and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"})
            and cfg["Healthcheck"]["Test"] == ["NONE"]
            and host["Tmpfs"] == {"/cache": "rw,nosuid,nodev,exec,size=16g,mode=0700",
                                 "/tmp": "rw,nosuid,nodev,noexec,size=256m,mode=1777"})
    devices = host["DeviceRequests"]
    require(len(devices) == 1 and devices[0]["DeviceIDs"] == [str(i) for i in range(4 * parallel_size(contract))]
            and devices[0]["Capabilities"] == [["gpu"]] and devices[0]["Count"] == 0
            and devices[0]["Driver"] in ("", "nvidia") and not devices[0].get("Options"))
    mounts = info["Mounts"]
    expected_mounts = {("bind", str(Path(bundle) / "payload"), "/opt/nib", False)}
    if not pre_serve:
        expected_mounts.add(("bind", str(weights), "/model", False))
    require(len(mounts) == len(expected_mounts))
    require({(m["Type"], m["Source"], m["Destination"], m["RW"]) for m in mounts}
            == expected_mounts)


def cleanup(recorder, name, owner, env, *, provider=False, checker_only=False):
    if provider or checker_only:
        checker_name = name + "-provider"
        absent_checker = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + checker_name + "$"]
        error = None
        try:
            if recorder.call(absent_checker, env, timeout=10, limit=4096).strip():
                info = inspect(recorder, "container", checker_name, env)
                require(info["Config"]["Labels"].get(LABEL + ".provider") == owner)
                recorder.call(["docker", "container", "rm", "--force", checker_name], env, timeout=20, limit=4096)
            require(not recorder.call(absent_checker, env, timeout=10, limit=4096).strip())
        except Exception as caught:
            error = caught
        finally:
            if not checker_only:
                cleanup(recorder, name, owner, env)
        if error is not None:
            raise ValueError("lab checker cleanup unconfirmed") from None
        return
    absent = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]
    if recorder.call(absent, env, timeout=10, limit=4096).strip():
        info = inspect(recorder, "container", name, env)
        require(info["Config"]["Labels"].get(LABEL) == owner)
        recorder.call(["docker", "container", "rm", "--force", name], env, timeout=30, limit=4096)
    require(not recorder.call(absent, env, timeout=10, limit=4096).strip())


def inspect(recorder, kind, name, env):
    return recorder.docker_json(kind, name, env, recorder.snapshot.execute_process_group)


def watchdog(bundle, sha, name, owner, deadline, life_fd, docker_host, *, checker_only=False):
    require(re.fullmatch(r"nib-deepseek-[0-9a-f]{32}", name) and re.fullmatch(r"[0-9a-f]{32}", owner))
    _, contract, _, recorder = bundle_inputs(bundle, sha)
    provider = "provider_conformance" in contract
    require(not checker_only or provider)
    env = controlled_env(docker_host)
    remaining = max(0, deadline - time.time())
    select.select([life_fd], [], [], min(remaining, 200 if checker_only else MAX_SECONDS))
    # Retry across the bounded create-client window: parent death can race a
    # request already accepted by Docker. Uncertain daemon completion is still
    # reported as unknown by the parent, never as successful cancellation.
    end = time.monotonic() + WATCHDOG_SETTLE_SECONDS
    while True:
        try:
            cleanup(recorder, name, owner, env, **({"provider": True, "checker_only": checker_only} if provider else {}))
        except Exception:
            if time.monotonic() >= end:
                raise ValueError("lab cleanup unconfirmed") from None
        if time.monotonic() >= end:
            break
        time.sleep(1)
    cleanup(recorder, name, owner, env, **({"provider": True, "checker_only": checker_only} if provider else {}))


def controlled_env(docker_host):
    require(type(docker_host) is str and re.fullmatch(r"unix:///[A-Za-z0-9_./-]+", docker_host))
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
            "DOCKER_HOST": docker_host, "DOCKER_CONFIG": "/nonexistent/nib-lab-docker-config"}


def container_observation(info, identifier, owner):
    require(info["Id"] == identifier and info["Config"]["Labels"].get(LABEL) == owner)
    state = info["State"]
    require(state["Status"] in ("created", "running", "paused", "restarting", "removing", "exited", "dead")
            and type(state["ExitCode"]) is int and -128 <= state["ExitCode"] <= 255
            and type(state["OOMKilled"]) is bool)
    return {"status": state["Status"], "exit_code": state["ExitCode"], "oom_killed": state["OOMKilled"]}


def run_container(bundle, sha, weights, deadline, recorder, contract, config, env):
    global CLEANING
    provider = "provider_conformance" in contract
    provider_ready = None
    gpu_owner = owner_module(contract, bundle)
    owner_errors = (gpu_owner.worker.Abort,) if gpu_owner is not None else ()
    owner_ready = owner_result = None
    if provider:
        session = load("lab_provider_live", Path(bundle) / "payload/provider-session.py",
                       contract["sources"]["provider-session.py"])
        session.enabled(contract)
        require(session.TEARDOWN_RESERVE < deadline - time.time() <= session.OVERALL_SECONDS)
        deadline -= session.TEARDOWN_RESERVE
        worker = load("lab_provider_live_worker", Path(bundle) / "payload/provider-worker.py",
                      contract["sources"]["provider-worker.py"])
        observer = load("lab_provider_live_observer", Path(bundle) / "payload/runtime-observation.py",
                        contract["sources"]["runtime-observation.py"])
        contract_sha = digest(read(Path(bundle) / "payload/lab.json"))
        monotonic_deadline = time.monotonic() + deadline - time.time()
    name, owner = "nib-deepseek-" + uuid.uuid4().hex, uuid.uuid4().hex
    report = {"result": "fail", "status": "BLOCKED", "deployable": False, "confidential": False,
              "functional_pass": False, "stage": "image", "cleanup": "not-created",
              "experiment_step": None, "container_observation": None, "output_bytes": None}
    if provider:
        report["provider_conformance"] = None
    monitor = None
    life_r, life_w = os.pipe()
    created = started = terminal = create_finished = False
    try:
        if provider:
            session.preflight_image(recorder, env, image_profile=session.enabled(contract).get("image_profile"))
        image = inspect(recorder, "image", contract["base_image"], env)
        report["image"] = recorder.image_binding(image, config, contract)
        require(not CANCELLED and deadline > time.time())
        monitor = subprocess.Popen([sys.executable, "-I", "-S", str(Path(__file__).absolute()), "--watchdog",
            str(bundle), sha, name, owner, str(deadline), str(life_r), env["DOCKER_HOST"]],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, pass_fds=(life_r,), start_new_session=True)
        report["stage"] = "create"
        require(not recorder.call(["docker", "container", "ls", "--all", "--quiet",
                                    "--filter", "name=^/" + name + "$"], env, timeout=10, limit=4096).strip())
        created = True
        identifier = recorder.call(create_command(name, owner, bundle, weights, contract, config), env).decode().strip()
        require(re.fullmatch(r"[0-9a-f]{64}", identifier))
        create_finished = True
        info = inspect(recorder, "container", name, env)
        check_container(info, name, owner, bundle, weights, contract, config, image["Id"])
        require(info["Id"] == identifier and info["State"]["Status"] == "created"
                and monitor.poll() is None and not CANCELLED)
        report["stage"] = "experiment"
        started = True
        report["experiment_step"] = "attach"
        if provider:
            def owner_check():
                current = inspect(recorder, "container", name, env)
                check_container(current, name, owner, bundle, weights, contract, config, image["Id"])
                require(current["Id"] == identifier and monitor.poll() is None and not CANCELLED)
                return current
            def checker_monitor(cutoff):
                reader, writer = os.pipe()
                try:
                    process = subprocess.Popen([sys.executable, "-I", "-S", str(Path(__file__).absolute()),
                        "--provider-watchdog", str(bundle), sha, name, owner, str(cutoff), str(reader), env["DOCKER_HOST"]],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        env=env, pass_fds=(reader,), start_new_session=True)
                except BaseException:
                    os.close(writer)
                    raise
                finally:
                    os.close(reader)
                return process, writer
            def ready(raw):
                nonlocal provider_ready
                provider_ready = session.validate_ready(raw, contract, contract_sha, observer)
                row = session.run_checker(Path(bundle) / "checker/payload", contract, worker, recorder, env,
                    name, owner, identifier, owner_check,
                    lambda: cleanup(recorder, name, owner, env, checker_only=True), checker_monitor,
                    min(monotonic_deadline, time.monotonic() + session.SECONDS), lambda: CANCELLED)
                report["provider_conformance"] = row
                return {"ready_sha256": digest(raw), "report_sha256": digest(session.encoded(row)),
                        "accepted": row["accepted"]}
            code, raw = session.capture(["docker", "container", "start", "--attach", "--interactive", name],
                env, monotonic_deadline, recorder.snapshot, ready=ready, cancelled=lambda: CANCELLED)
            require(code == 0)
        elif gpu_owner is not None:
            code, raw, owner_ready, owner_result = gpu_owner.capture(
                ["docker", "container", "start", "--attach", "--interactive", name], env,
                time.monotonic() + deadline - time.time(), recorder.snapshot, contract,
                digest(read(Path(bundle) / "payload/lab.json")))
            require(code == 0)
        else:
            raw = recorder.call(["docker", "container", "start", "--attach", name], env,
                                timeout=max(1, min(MAX_SECONDS, deadline - time.time())), limit=result_limits(bundle, contract)[0])
        report["output_bytes"] = len(raw)
        report["experiment_step"] = "decode"
        experiment = json.loads(raw)
        report["experiment_step"] = "terminal"
        final = inspect(recorder, "container", name, env)
        report["container_observation"] = container_observation(final, identifier, owner)
        require(final["Id"] == identifier and final["State"]["Status"] == "exited"
                and final["State"]["ExitCode"] == 0 and not final["State"].get("OOMKilled"))
        report["experiment_step"] = "validation"
        validate_result(experiment, bundle, contract, recorder)
        if gpu_owner is not None:
            gpu_owner.validate_join(owner_ready, owner_result, experiment)
        if provider:
            if provider_ready is not None:
                session.validate_join(report["provider_conformance"], provider_ready, experiment, worker,
                                      image_profile=session.enabled(contract).get("image_profile"))
            else:
                require(experiment["provider_conformance"] is None and experiment["functional_pass"] is False)
        terminal = True
        report.update(experiment=experiment, functional_pass=experiment["functional_pass"],
                      stage="completed", experiment_step="completed")
    except (Exception, Cancelled, *owner_errors):
        report["failure"] = "bounded-lab-stage-failed"
    finally:
        CLEANING = True
        os.close(life_r)
        if started and report["container_observation"] is None:
            # Observe the owned container before cleanup changes its exit state.
            try:
                report["container_observation"] = container_observation(
                    inspect(recorder, "container", name, env), identifier, owner)
            except Exception:
                pass
        if created:
            report["cleanup"] = "unconfirmed"
            try:
                cleanup(recorder, name, owner, env, **({"provider": True} if provider else {}))
                report["cleanup"] = "confirmed"
            except Exception:
                report["functional_pass"] = False
        os.close(life_w)
        if monitor is not None:
            try:
                monitor.wait(timeout=120)
                require(monitor.returncode == 0)
            except Exception:
                report["cleanup"] = "unconfirmed"
                report["functional_pass"] = False
                if monitor.poll() is None:
                    os.killpg(monitor.pid, signal.SIGKILL)
                    monitor.wait(timeout=10)
        if created and not create_finished:
            report["cleanup"] = "create-completion-unknown"
            report["functional_pass"] = False
        if started and not terminal:
            report["functional_pass"] = False
        if CANCELLED:
            report["functional_pass"] = False
            report["failure"] = "bounded-lab-stage-failed"
    return report


def result_limits(bundle, contract):
    if "stream_cohort" not in contract:
        return 65536, 60000
    module = load("lab_stream_limits", Path(bundle) / "payload/stream-lab.py",
                  contract["sources"]["stream-lab.py"])
    # Historical source-bound bundles predate the explicit sustained profile.
    return module.result_limits(contract) if hasattr(module, "result_limits") else (65536, 60000)


def validate_result(row, bundle, contract, recorder):
    identity = validate_model_profile(contract, bundle)
    if contract.get("operation") == "hardware-diagnostic":
        probe = load("lab_diagnostic_validation", Path(bundle) / "payload/probe.py", contract["sources"]["probe.py"])
        probe.validate_diagnostic(row)
        return
    correctness = contract.get("operation") == "local-correctness"
    parity = contract.get("tokenizer_parity") is True
    pre_serve = contract.get("operation") == "pre-serve"
    fields = {"schema", "status", "deployable", "confidential", "functional_pass",
              "stage", "application", "hardware", "completion", "startup", "engine_stopped"}
    if identity is not None:
        fields.add("model_profile")
        require(type(row) is dict and row.get("model_profile") == identity)
    expected_schema = "nib.deepseek-lab-result/v2"
    if "tau_owner" in contract:
        fields.add("tau_owner")
        module = owner_module(contract, bundle)
        if row.get("tau_owner") is not None:
            try:
                module.validate_result(row["tau_owner"])
            except module.worker.Abort:
                # This source-verified module owns a fresh Abort class. Normalize
                # only its validation failure at the host reporting boundary.
                raise ValueError("invalid tau owner result") from None
            require(row.get("stage") in ("request", "complete"))
        require(row.get("functional_pass") is not True or row.get("tau_owner") is not None
                and row["tau_owner"]["status"] == "evaluated")
    if "runtime_observation" in contract:
        fields.add("runtime_observation")
        observer = load("lab_runtime_validation", Path(bundle) / "payload/runtime-observation.py",
                        contract["sources"]["runtime-observation.py"])
        observer.validate(row.get("runtime_observation"), contract, digest(read(Path(bundle) / "payload/lab.json")),
                          complete=row.get("functional_pass") is True or row.get("stage") == "complete")
        require(not row["runtime_observation"]["samples"] or row.get("stage") in ("request", "complete"))
    if "provider_conformance" in contract:
        fields.add("provider_conformance")
        session = load("lab_provider_validation", Path(bundle) / "payload/provider-session.py",
                       contract["sources"]["provider-session.py"])
        session.enabled(contract)
        if row.get("provider_conformance") is not None:
            session.validate_ack(row["provider_conformance"])
            require(row.get("stage") in ("request", "complete"))
        if row.get("functional_pass") is True:
            require(row.get("provider_conformance") is not None and row["provider_conformance"]["accepted"])
    if "stream_cohort" in contract:
        fields.add("stream_cohort")
        stream = load("lab_stream_validation", Path(bundle) / "payload/stream-lab.py",
                      contract["sources"]["stream-lab.py"])
        stream.enabled(contract)
        accepted = False
        if row.get("stream_cohort") is not None:
            sources = {"stream/" + name + ".py": read(Path(bundle) / "payload/stream" / (name + ".py"))
                       for name in (stream.source_names(contract["stream_cohort"].get("profile"))
                                    if hasattr(stream, "source_names") else stream.NAMES)}
            cohort = stream.load(sources, contract["sources"])
            accepted = stream.validate(row["stream_cohort"], contract, digest(read(Path(bundle) / "payload/lab.json")), cohort,
                                       **({"model": identity["served_model_name"]} if identity else {}))
            require(row.get("stage") in ("request", "complete"))
        require(row.get("functional_pass") is not True or accepted)
    if "tokenizer_materialization" in contract:
        fields.add("tokenizer_materialization")
        require(parity and row.get("tokenizer_materialization") == contract["tokenizer_materialization"])
    if correctness:
        fields.add("tokenizer_parity" if parity else "correctness")
        expected_schema = "nib.deepseek-lab-tokenizer-parity/v1" if parity else "nib.deepseek-lab-correctness-result/v2"
    if pre_serve:
        fields.add("entry_pass")
        expected_schema = "nib.deepseek-lab-pre-serve/v1"
        require(type(row) is dict and type(row.get("entry_pass")) is bool and row.get("functional_pass") is False
                and row.get("completion") is None and row.get("stage") not in ("weights", "request"))
    require(type(row) is dict and set(row) == fields
            and row["schema"] == expected_schema and row["status"] == "BLOCKED"
            and row["deployable"] is False and row["confidential"] is False
            and type(row["functional_pass"]) is bool and type(row["engine_stopped"]) is bool
            and row["stage"] in ("hardware", "weights", "application", "prelaunch", "startup", "request", "complete"))
    startup = row["startup"]
    if row["stage"] in ("hardware", "weights", "application", "prelaunch"):
        require(startup is None)
    else:
        startup_fields = {"outcome", "wait_ms", "exit_code", "health_probes", "checkpoints", "exception_diagnostic"}
        require(type(startup) is dict
                and set(startup) in (startup_fields, startup_fields | {"worker_exception_diagnostic"}))
        errors = load("lab_exception_validation", Path(bundle) / "payload/exception.py",
                      contract["sources"]["exception.py"])
        errors.require = require
        errors.validate_exception(startup["exception_diagnostic"])
        require(startup["exception_diagnostic"] is None or startup["outcome"] == "child-exited")
        worker_error = startup.get("worker_exception_diagnostic")
        if type(worker_error) is dict and worker_error.get("schema") == "nib.engine-worker-exception/v2":
            errors.validate_worker_exception(worker_error)
        else:
            errors.validate_exception(worker_error)
        require(startup.get("worker_exception_diagnostic") is None or startup["outcome"] == "child-exited")
        outcomes = ("launch-failed", "child-exited", "startup-timeout", "supervisor-failure")
        outcomes += ("prepared",) if pre_serve else ("models-rejected", "ready")
        require(startup["outcome"] in outcomes
                and type(startup["wait_ms"]) is int and 0 <= startup["wait_ms"] <= MAX_SECONDS * 1000
                and type(startup["health_probes"]) is int and 0 <= startup["health_probes"] <= MAX_SECONDS + 1)
        if pre_serve:
            require(startup["health_probes"] == 0)
        checkpoints = startup["checkpoints"]
        require(type(checkpoints) is dict and set(checkpoints) == {"status", "last_id"}
                and checkpoints["status"] in ("not-started", "eof", "incomplete", "invalid", "overflow", "read-failed")
                and (checkpoints["last_id"] is None
                     or type(checkpoints["last_id"]) is int and 1 <= checkpoints["last_id"] <= 17)
                and (checkpoints["status"] != "not-started" or checkpoints["last_id"] is None))
        if startup["outcome"] in ("child-exited", "prepared"):
            require(type(startup["exit_code"]) is int and -128 <= startup["exit_code"] <= 255)
        else:
            require(startup["exit_code"] is None)
        if startup["outcome"] == "launch-failed":
            require(startup["wait_ms"] == 0 and startup["health_probes"] == 0)
        if row["stage"] in ("request", "complete"):
            if pre_serve:
                require(startup["outcome"] == "prepared" and startup["exit_code"] == 0
                        and startup["health_probes"] == 0
                        and checkpoints == {"status": "eof", "last_id": 16})
            else:
                require(startup["outcome"] == "ready" and startup["health_probes"] > 0)
        else:
            require(startup["outcome"] != "ready" and row["functional_pass"] is False)
    if row["hardware"] is not None:
        probe = load("lab_hardware_validation", Path(bundle) / "payload/probe.py", contract["sources"]["probe.py"])
        if probe.security_profile(contract) == "nonconfidential":
            probe.validate_nonconfidential_report(row["hardware"], family=contract.get("gpu_family", "B200"),
                                                 expected=4 * parallel_size(contract))
        else:
            probe.validate_report(row["hardware"], family=contract.get("gpu_family", "B200"), expected=4 * parallel_size(contract))
    if row["application"] is not None:
        # Reuse the existing exact application-receipt validator unchanged. This
        # temporary validation envelope is not evidence of a P0 import attempt.
        validator = load("lab_application_validation", Path(bundle) / "support" / SMOKE,
                         contract["validation_source_sha256"])
        overlay = recorder.overlay
        raw_application = read(Path(bundle) / "payload/application/contract.json")
        require(digest(raw_application) == contract["application_contract_sha256"])
        application = overlay.load_json(raw_application)
        app = load("lab_measured_layout", Path(bundle) / "payload/application/application.py",
                   contract["sources"]["application/application.py"])
        measured = app.measurement(overlay, read(Path(bundle) / "payload/application/measurement.json"))
        validator.validate(overlay, {
            "schema": "nib.engine-import-smoke/v1", "status": "BLOCKED", "deployable": False,
            "image": None, "abi_validated": False, "hardware_validated": False, "inference_started": False,
            "stage": "import-smoke", "application": row["application"], "smoke": None,
            "failure": "bounded-smoke-stage-rejected",
            "phase_elapsed_ms": {"application-preflight": 0, "application": 0, "import-smoke": 0}},
            application, measured)
        # The writer hashes its complete pre-application report here, not only
        # its files. The measured files-only digest is enforced by app.inspect
        # before application; comparing these different domains rejects valid receipts.
    if row["completion"] is not None:
        value = row["completion"]
        require(type(value) is dict and set(value) == {"generated", "finish", "prompt_tokens", "completion_tokens",
                                                     "final_content_present", "synthetic_answer_match"}
                and value["generated"] is True and value["finish"] in ("stop", "length")
                and type(value["final_content_present"]) is bool and type(value["synthetic_answer_match"]) is bool
                and (not value["synthetic_answer_match"] or value["final_content_present"])
                and type(value["prompt_tokens"]) is int and 0 < value["prompt_tokens"] <= 4096
                and type(value["completion_tokens"]) is int and 0 < value["completion_tokens"] <= 128)
    if row["functional_pass"]:
        require(row["stage"] == "complete" and row["engine_stopped"] is True
                and type(row["application"]) is dict and row["application"].get("deployable") is False
                and type(row["hardware"]) is dict and row["hardware"].get("attestation_verified") is False)
        if not correctness:
            require(type(row["completion"]) is dict and row["completion"].get("generated") is True
                    and row["completion"]["finish"] == "stop"
                    and row["completion"]["final_content_present"] is True
                    and row["completion"]["synthetic_answer_match"] is True)
    if correctness:
        require(row["completion"] is None)
        field = "tokenizer_parity" if parity else "correctness"
        if row[field] is not None:
            checker = load("lab_correctness_validation", Path(bundle) / "payload/correctness.py",
                           contract["sources"]["correctness.py"])
            if parity:
                parity_checker = load("lab_parity_validation", Path(bundle) / "payload/tokenizer-parity.py",
                                      contract["sources"]["tokenizer-parity.py"])
                require(parity_checker.enabled(contract))
                tuning = tuning_module(contract, bundle)
                options = {"max_model_len": tuning.context_limit(contract)} if tuning else {}
                parity_checker.validate(row[field], checker, **options)
            else:
                checker.validate(row[field])
            require(row["hardware"] is not None and row["application"] is not None)
        if row["functional_pass"]:
            require(row[field] is not None and row[field]["all_cases_pass"])
    require(row["hardware"] is not None or (row["application"] is None and row["completion"] is None))
    require(row["application"] is not None or row["completion"] is None)
    if pre_serve:
        require(row["entry_pass"] == (row["stage"] == "complete" and row["engine_stopped"] is True
                                     and row["hardware"] is not None and row["application"] is not None))
    require(len(json.dumps(row)) <= result_limits(bundle, contract)[1])


def cancel(*_):
    global CANCELLED
    was_cancelled = CANCELLED
    CANCELLED = True
    if not was_cancelled and not CLEANING:
        raise Cancelled("lab cancelled")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--watchdog", "--provider-watchdog"):
        require(len(sys.argv) == 9)
        watchdog(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5],
                 float(sys.argv[6]), int(sys.argv[7]), sys.argv[8],
                 **({"checker_only": True} if sys.argv[1] == "--provider-watchdog" else {}))
        return 0
    class QuietParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("lab arguments rejected")
    parser = QuietParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    preparations = {
        "prepare": ("functional", "B200", "confidential"),
        "prepare-hardware-diagnostic": ("hardware-diagnostic", "B200", "confidential"),
        "prepare-local-correctness": ("local-correctness", "B200", "confidential"),
        "prepare-local-correctness-b300": ("local-correctness", "B300", "confidential"),
        "prepare-local-correctness-b300-dp2": ("local-correctness", "B300", "confidential", 2),
        "prepare-local-correctness-b200-dp2": ("local-correctness", "B200", "confidential", 2),
        "prepare-pre-serve": ("pre-serve", "B200", "confidential"),
        "prepare-pre-serve-b300": ("pre-serve", "B300", "confidential"),
        "prepare-nonconfidential-baseline": ("local-correctness", "B200", "nonconfidential"),
        "prepare-nonconfidential-baseline-b300": ("local-correctness", "B300", "nonconfidential"),
        "prepare-nonconfidential-uncensored-b300-dp2": ("local-correctness", "B300", "nonconfidential", 2),
    }
    for option in preparations:
        action.add_argument("--" + option)
    action.add_argument("--run", metavar="PRIVATE_BUNDLE")
    parser.add_argument("--tokenizer-parity", action="store_true",
                        help="Prepare the fixed tokenizer prerequisite instead of correctness cases.")
    parser.add_argument("--tokenizer-materialization", action="store_true",
                        help="Prepare the pinned server validator adapter; requires --tokenizer-parity.")
    parser.add_argument("--runtime-observation", action="store_true",
                        help="Observe the owned API process and cgroup counters; not tau admission.")
    parser.add_argument("--provider-conformance-wheels", metavar="PRIVATE_LOCKED_WHEELHOUSE",
                        help="Prepare the opt-in owned CPU checker; requires DP2, parity, materialization and observation.")
    parser.add_argument("--provider-image-profile", choices=("docker28-classic",),
                        help="Prepare an explicitly pinned classic-store checker image; never a runtime fallback.")
    parser.add_argument("--stream-cohort", action="store_true",
                        help="Prepare one warmup plus eight 2K/512 streaming requests; requires parity, materialization and observation.")
    parser.add_argument("--tau-owner", action="store_true",
                        help="Prepare selected 8K owner; run requires private controller pipes, not a terminal.")
    parser.add_argument("--stream-profile", metavar="PROFILE",
                        help="Closed C1/C8 profiles, or chat-2k-512-c16-32-64-isolated-900s (requires batch32-graphs).")
    parser.add_argument("--model-profile", choices=("original", "uncensored"),
                        help="Fixed checkpoint selection during preparation only; absence selects original.")
    parser.add_argument("--execution-profile", metavar="PROFILE",
                        help="Explicit ordinary uncensored B300 DP2 experiment: batch8-eager, batch8-graphs or batch8-dspark.")
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--weights")
    parser.add_argument("--output")
    parser.add_argument("--deadline", help="Required UTC hard cutoff, YYYY-MM-DDTHH:MM:SSZ, at most 80 minutes away.")
    args = parser.parse_args()
    require(args.stream_profile is None or args.stream_cohort)
    require(args.provider_image_profile is None or args.provider_conformance_wheels)
    require(not args.tokenizer_materialization or args.tokenizer_parity)
    require(not args.stream_cohort or args.tokenizer_parity and args.tokenizer_materialization
            and args.runtime_observation and not args.provider_conformance_wheels)
    for option, profile in preparations.items():
        destination = getattr(args, option.replace("-", "_"))
        if destination:
            require(not any((args.receipt_sha256, args.weights, args.output, args.deadline)))
            require(option != "prepare-local-correctness-b200-dp2" or args.model_profile == "uncensored")
            require(option != "prepare-nonconfidential-uncensored-b300-dp2" or args.model_profile == "uncensored")
            target = model_target(profile[1], profile[2], profile[3] if len(profile) == 4 else 1,
                                  args.model_profile)
            optional = {}
            if args.tau_owner:
                require(option == "prepare-nonconfidential-uncensored-b300-dp2"
                        and args.execution_profile == "tau8k-eager" and args.tokenizer_parity
                        and args.tokenizer_materialization and args.runtime_observation
                        and not args.stream_cohort and not args.provider_conformance_wheels)
                optional["tau_owner"] = True
            if args.execution_profile is not None:
                require(option == "prepare-nonconfidential-uncensored-b300-dp2"
                        and args.model_profile == "uncensored")
                tuning_measurement_options(args.tokenizer_parity, args.tokenizer_materialization,
                    args.runtime_observation, args.stream_cohort, args.stream_profile, args.provider_conformance_wheels,
                    args.execution_profile)
                optional["execution_profile"] = args.execution_profile
            if args.model_profile == "uncensored":
                require(profile[0] == "local-correctness" and len(profile) == 4 and profile[3] == 2
                        and model_target(profile[1], profile[2], profile[3], args.model_profile))
                optional["model_profile"] = args.model_profile
            if args.stream_cohort:
                require(profile[0] == "local-correctness" and target)
                optional["stream_cohort"] = True
                if args.stream_profile is not None:
                    optional["stream_profile"] = args.stream_profile
            if args.provider_conformance_wheels:
                require(profile[0] == "local-correctness"
                        and provider_target(profile[1], profile[2], profile[3] if len(profile) == 4 else 1,
                                            args.model_profile)
                        and args.tokenizer_parity and args.tokenizer_materialization and args.runtime_observation)
                optional["provider_wheelhouse"] = args.provider_conformance_wheels
                if args.provider_image_profile is not None:
                    optional["provider_image_profile"] = args.provider_image_profile
            if args.runtime_observation:
                require(profile[0] == "local-correctness" and target)
                optional["runtime_observation"] = True
            if args.tokenizer_parity:
                require(profile[0] == "local-correctness" and target)
                if args.tokenizer_materialization:
                    optional["tokenizer_materialization"] = True
                prepare(destination, *profile, tokenizer_parity=True, **optional)
            else:
                prepare(destination, *profile, **optional)
            return 0
    require(not args.tau_owner and not args.tokenizer_parity and not args.tokenizer_materialization and not args.runtime_observation
            and not args.provider_conformance_wheels and not args.stream_cohort and args.stream_profile is None
            and args.model_profile is None and args.execution_profile is None and args.provider_image_profile is None)
    require(sys.platform == "linux" and all((args.receipt_sha256, args.output, args.deadline)))
    deadline = dt.datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
    require(0 < deadline - time.time() <= MAX_SECONDS)
    bundle, output = (Path(p) for p in (args.run, args.output))
    weights = Path(args.weights) if args.weights else None
    paths = (bundle, output) + ((weights,) if weights is not None else ())
    require(all(p.is_absolute() and p == p.resolve() for p in paths)
            and not os.path.lexists(output) and not any(a == b or a in b.parents or b in a.parents
                                                       for i, a in enumerate(paths) for b in paths[i + 1:]))
    receipt, contract, config, recorder = bundle_inputs(bundle, args.receipt_sha256)
    validate_runtime_payload(bundle)
    require((weights is None) == (contract["operation"] == "pre-serve"))
    if contract["operation"] == "pre-serve":
        require(deadline - time.time() <= 1080)
    if contract["operation"] == "hardware-diagnostic":
        require(deadline - time.time() <= 300)
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    started = recorder.now()
    # Fixed local Docker socket: no account config, remote context, plugins or credentials.
    env = controlled_env("unix:///var/run/docker.sock")
    report = run_container(bundle, args.receipt_sha256, weights, deadline, recorder, contract, config, env)
    report["provenance"] = {"git_sha": receipt["git_sha"], "source_bundle_sha256": receipt["source_bundle_sha256"],
                            "payload_sha256": receipt["payload_sha256"], "receipt_sha256": args.receipt_sha256,
                            "image": contract["base_image"], "config_sha256": contract["official_config"]["sha256"],
                            "manifest_sha256": contract["manifest_sha256"], "pack_revision": contract["pack_revision"]}
    if "model_profile" in contract:
        report["provenance"]["model_profile"] = contract["model_profile"]
    if "execution_profile" in contract:
        report["provenance"]["execution_profile"] = contract["execution_profile"]
    hardware_row = report.get("experiment", {}).get("hardware")
    diagnostic = contract["operation"] == "hardware-diagnostic"
    record = {"schema": "nib.evidence/v1", "phase": "P2", "task": "T3", "git_sha": receipt["git_sha"],
              "started_utc": started, "ended_utc": recorder.now(), "result": "fail",
              "execution": "hardware" if hardware_row else "software", "hardware_validated": False,
              "host": {"provider": "spheron", "machine": "approved-isolated-lab",
                       "gpus": (("eight" if parallel_size(contract) == 2 else "four") + "-selected-"
                                + hardware_row["family"]) if hardware_row else "unobserved",
                       "driver": hardware_row["driver"] if hardware_row else None,
                       "cuda": hardware_row["cuda_driver"] if hardware_row else None},
              "command": "python3 -I -S host/record-deepseek-lab.py --run <private-bundle> --receipt-sha256 <digest> --weights <private-weights> --output <private-evidence> --deadline <approved-UTC-cutoff>",
              "notes": "Partial Spheron functional experiment, not GCP gates or full P2 acceptance. "
                       "Candidate BLOCKED; deployable=false; confidential=false; no attestation or ABI qualification. "
                       "Generated JIT code is private output, not verified native provenance. "
                       "CUDA field is the observed driver-supported CUDA API version, not a runtime measurement."}
    if contract.get("gpu_security") == "nonconfidential":
        record["notes"] += " Explicit nonconfidential baseline; CC-disabled observations are not confidential evidence."
    if diagnostic:
        record["notes"] = ("Denial-only read-only hardware diagnostic; no weights, application, engine or inference. "
                           "Fixed call stages, return codes and bounded numeric observations only. "
                           "All original CC/readiness policy checks retained; no settings changes. "
                           "Candidate BLOCKED; deployable=false; confidential=false; not attestation or qualification.")
    if contract["operation"] == "pre-serve":
        record["notes"] = ("GPU pre-serve diagnostic only: same image application, imports and argument validation. "
                           "No model mount, weight validation, serve.cmd, HTTP probes or inference. "
                           "Candidate BLOCKED; no confidential or ABI qualification.")
        record["command"] = record["command"].replace(" --weights <private-weights>", "")
    with tempfile.TemporaryDirectory(prefix="nib-lab-evidence-") as temporary:
        recorder.publish_evidence(record, report, bundle / "payload/nib", Path(temporary).resolve(), output, env)
    print(json.dumps({"recorded": True, "functional_pass": report["functional_pass"],
                      "status": "BLOCKED", "confidential": False}, sort_keys=True))
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Exception, Cancelled):
        sys.exit("Lab operation rejected; no successful qualification may be inferred.")
