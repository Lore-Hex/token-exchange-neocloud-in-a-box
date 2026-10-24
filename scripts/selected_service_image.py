# SPDX-License-Identifier: Apache-2.0
"""Offline packaging for the closed selected-service owner; never publish an image."""

import sys
from pathlib import Path

if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B selected_service_image.py")
    sys.path.insert(0, str(Path(__file__).absolute().parent))

import argparse
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import types

import selected_service_process as owner

ROOT = Path(__file__).absolute().parents[1]
SCHEMA = "nib.selected-service-image-preparation/v1"
BUILD_SCHEMA = "nib.selected-service-image-build/v1"
LIMIT, TOTAL, COUNT = 128 << 20, 384 << 20, 4096
require, digest = owner.require, owner.digest
read = owner.bootstrap.read_source


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def host_recorder():
    path = ROOT / "host/record-deepseek-lab.py"
    module = types.ModuleType("selected_service_lab_recorder")
    module.__file__ = str(path)
    exec(compile(read(str(path)), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def committed_sources(recorder):
    checker = recorder.checker
    revision = checker.git(ROOT, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
    require(re.fullmatch("[0-9a-f]{40}", revision), "identity")
    require(not checker.git(ROOT, "status", "--porcelain", "--untracked-files=normal"), "identity")
    entries = checker.git(ROOT, "ls-tree", "-r", "-z", revision, "--", "scripts", "go.mod",
                          "go.sum", "cmd/nib-pilot-api", "internal", "schemas", "host/record-deepseek-lab.py")
    files, total = {}, 0
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.decode().split("\t")
        if name.startswith("scripts/") and not (name.endswith(".py") or name == "scripts/e2e/pins.json"):
            continue
        mode, kind, oid = meta.split()
        recorder.overlay.safe_name(name)
        require(kind == "blob" and mode in {"100644", "100755"} and len(files) < COUNT
                and int(checker.git(ROOT, "cat-file", "-s", oid)) <= 2 << 20, "identity")
        raw = checker.git(ROOT, "cat-file", "blob", oid)
        total += len(raw)
        require(total <= 64 << 20 and read(str(ROOT / name)) == raw, "identity")
        files[name] = raw
    require("scripts/selected_service_image.py" in files and "scripts/e2e/pins.json" in files, "identity")
    return revision, files


def build_guard(recorder, files, work, env):
    go, env = recorder.validator_toolchain(env)
    sources = {name: raw for name, raw in files.items()
               if name in {"go.mod", "go.sum"} or name.startswith(("cmd/", "internal/", "schemas/"))}
    source = work / "guard-source"
    recorder.prep.publish(source, sources)
    binary = work / "nib-pilot-api"
    env.update(GOOS="linux", GOARCH="amd64", CGO_ENABLED="0", GOTOOLCHAIN="local",
               GOPROXY="off", GOSUMDB="off")
    recorder.call([go, "build", "-trimpath", "-buildvcs=false", "-o", str(binary), "./cmd/nib-pilot-api"],
        env, execute=lambda args, **kw: recorder.snapshot.execute_process_group(args, cwd=source, **kw),
        timeout=180)
    raw = read(str(binary), LIMIT)
    require(raw[:6] == b"\x7fELF\x02\x01" and raw[18:20] == b"\x3e\x00", "identity")
    return raw, recorder.overlay.bundle_digest(sources)


def dockerfile(contract, prepared_sha):
    interpreter = contract["interpreter"]["resolved"]
    require(interpreter == "/usr/bin/python3.12", "identity")
    entry = [interpreter, "-I", "-S", "-B", "/opt/nib-service/scripts/selected_service_process.py",
             "--prepared-sha256", prepared_sha]
    application_sha = contract["sources"]["application/contract.json"]
    return ("FROM " + contract["base_image"] + "\n"
        "USER 0:0\n"
        "COPY payload/ /opt/nib/\n"
        "COPY service/ /opt/nib-service/\n"
        "RUN --network=none " + interpreter + " -I -S -B /opt/nib/application/application.py "
        + application_sha + " --apply\n"
        "RUN --network=none " + interpreter + " -I -S -B /opt/nib-service/scripts/selected_service_image.py "
        "seal --prepared-sha256 " + prepared_sha + "\n"
        "USER 65532:65532\nWORKDIR /\nHEALTHCHECK NONE\n"
        "ENTRYPOINT " + json.dumps(entry) + "\nCMD []\n").encode()


def seal(prepared_sha):
    """Image-build-only permission preparation, after the reviewed overlay apply."""
    require(os.getuid() == 0, "identity")
    raw = read(str(owner.ROOT / "prepared.json"))
    require(digest(raw) == prepared_sha, "identity")
    prepared = owner.service.correctness.strict_json(raw)
    require(set(prepared) == {"schema", "lab_sha256", "files"}
            and prepared["schema"] == "nib.selected-service-prepared/v1"
            and set(prepared["files"]) == owner.source_files(owner.ROOT, time.monotonic() + 30), "identity")
    lab_raw = read(str(owner.PAYLOAD / "lab.json"))
    require(digest(lab_raw) == prepared["lab_sha256"], "identity")
    contract = owner.service.correctness.strict_json(lab_raw)
    owner.engine_entry.canonical_arguments(contract)
    require(type(contract["sources"]) is dict and len(contract["sources"]) <= 110
            and all(type(name) is str and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                    and all(part not in ("", ".", "..") for part in name.split("/"))
                    for name in contract["sources"]), "identity")
    paths = {owner.ROOT / name: sha for name, sha in prepared["files"].items()}
    paths[owner.ROOT / "prepared.json"] = prepared_sha
    paths.update({owner.PAYLOAD / name: sha for name, sha in contract["sources"].items()})
    paths[owner.PAYLOAD / "lab.json"] = digest(lab_raw)
    wrapper = owner.service.engine_lab.wrapper_bytes(contract["interpreter"]["resolved"],
                                                     contract["environment"], digest(lab_raw))
    paths[owner.PAYLOAD / "python"] = digest(wrapper)
    require(len(paths) <= COUNT, "contract")
    total, directories = 0, {owner.ROOT, owner.PAYLOAD}
    for path, expected in paths.items():
        raw = read(str(path), LIMIT)
        total += len(raw)
        require(total <= TOTAL and digest(raw) == expected, "identity")
        parent = path.parent
        while parent not in {owner.ROOT, owner.PAYLOAD}:
            require(parent not in {Path("/"), Path("/opt")}, "identity")
            directories.add(parent)
            parent = parent.parent
    # Public source and executable files only. No operator inputs enter a layer.
    executables = {owner.ROOT / "bin/trcs", owner.ROOT / "bin/nib-pilot-api",
                   owner.PAYLOAD / "nib", owner.PAYLOAD / "python"}
    for path in paths:
        path.chmod(0o555 if path in executables else 0o444)
    for path in directories:
        require(stat.S_ISDIR(path.lstat().st_mode) and not path.is_symlink(), "identity")
        path.chmod(0o555)


def prepare(bundle, receipt_sha, trcs, output, *, checker_wheelhouse=None):
    lab = host_recorder()
    receipt, contract, _, recorder = lab.bundle_inputs(bundle, receipt_sha)
    revision, files = committed_sources(recorder)
    payload = {name.removeprefix("payload/"): lab.read(Path(bundle) / name,
        lab.MAX_BYTES if name == "payload/nib" else 2 << 20)
        for name in receipt["files"] if name.startswith("payload/")}
    derived = owner.prepare_payload(payload)
    contract = owner.service.correctness.strict_json(derived["lab.json"])
    pins = owner.service.correctness.strict_json(files["scripts/e2e/pins.json"])
    tls = read(str(Path(trcs)), LIMIT)
    require(digest(tls) == pins["trcs"]["binary_sha256"], "identity")
    with tempfile.TemporaryDirectory(prefix="nib-selected-service-image-") as temporary:
        work = Path(temporary).resolve()
        env = recorder.offline_environment(work / "control")
        guard, guard_source_sha = build_guard(recorder, files, work, env)
        service_files = {name: raw for name, raw in files.items() if name.startswith("scripts/")}
        service_files.update({"bin/nib-pilot-api": guard, "bin/trcs": tls})
        if checker_wheelhouse is not None:
            import provider_conformance
            checker = provider_conformance.prepare(Path(checker_wheelhouse), time.monotonic() + 60,
                                                    model_profile="uncensored")
            service_files.update({"checker/" + name: raw for name, raw in checker.items()})
        source = work / "service"
        recorder.prep.publish(source, service_files)
        prepared = encoded(owner.preparation_receipt(source, digest(derived["lab.json"])))
    context = {"context/service/" + name: raw for name, raw in service_files.items()}
    context.update({"context/payload/" + name: raw for name, raw in derived.items()})
    context["context/service/prepared.json"] = prepared
    context["context/Dockerfile"] = dockerfile(contract, digest(prepared))
    # Metadata stays outside Docker's context and is revalidated before build.
    for name in ("manifest.json", "config.json"):
        context["metadata/" + name] = lab.read(Path(bundle) / "metadata" / name)
    record = {"schema": SCHEMA, "git_sha": revision, "lab_receipt_sha256": receipt_sha,
        "prepared_sha256": digest(prepared), "guard_source_sha256": guard_source_sha,
        "image_built": False, "hardware_validated": False, "deployable": False,
        "files": {name: {"bytes": len(raw), "sha256": digest(raw)} for name, raw in context.items()}}
    context["receipt.json"] = encoded(record)
    require(len(context) <= COUNT and sum(map(len, context.values())) <= TOTAL, "contract")
    recorder.prep.publish(Path(output), context)
    return {"status": "BLOCKED", "receipt_sha256": digest(context["receipt.json"]),
            "prepared_sha256": digest(prepared), "image_built": False, "hardware_validated": False}


def validated_files(path, expected):
    require(type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected), "identity")
    raw = read(str(path / "receipt.json"))
    require(digest(raw) == expected, "identity")
    receipt = owner.service.correctness.strict_json(raw)
    require(type(receipt) is dict and set(receipt) == {"schema", "git_sha", "lab_receipt_sha256",
        "prepared_sha256", "guard_source_sha256", "image_built", "hardware_validated", "deployable", "files"}
        and receipt["schema"] == SCHEMA and receipt["image_built"] is False
        and receipt["hardware_validated"] is False and receipt["deployable"] is False
        and type(receipt["files"]) is dict and 0 < len(receipt["files"]) < COUNT, "contract")
    require(type(receipt["git_sha"]) is str and re.fullmatch("[0-9a-f]{40}", receipt["git_sha"])
            and all(type(receipt[key]) is str and re.fullmatch("[0-9a-f]{64}", receipt[key])
                    for key in ("lab_receipt_sha256", "prepared_sha256", "guard_source_sha256"))
            and all(type(row) is dict and set(row) == {"bytes", "sha256"}
                    and type(row["bytes"]) is int and 0 < row["bytes"] <= LIMIT
                    and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"])
                    for row in receipt["files"].values()), "contract")
    files, pending, seen, total = {}, [(path, 0)], 0, 0
    while pending:
        directory, depth = pending.pop()
        require(depth <= 16, "contract")
        with os.scandir(directory) as entries:
            for entry in entries:
                seen += 1
                require(seen <= COUNT * 2 and not entry.is_symlink(), "identity")
                if entry.is_dir(follow_symlinks=False):
                    pending.append((Path(entry.path), depth + 1))
                else:
                    require(entry.is_file(follow_symlinks=False), "identity")
                    name = Path(entry.path).relative_to(path).as_posix()
                    if name == "receipt.json":
                        require(read(entry.path) == raw, "identity")
                        continue
                    require(name in receipt["files"], "identity")
                    data = read(entry.path, LIMIT)
                    total += len(data)
                    require(total <= TOTAL and receipt["files"][name] ==
                            {"bytes": len(data), "sha256": digest(data)}, "identity")
                    files[name] = data
    require(set(files) == set(receipt["files"]), "identity")
    prepared = files["context/service/prepared.json"]
    require(digest(prepared) == receipt["prepared_sha256"], "identity")
    lab = owner.service.correctness.strict_json(files["context/payload/lab.json"])
    owner.engine_entry.canonical_arguments(lab)
    require(files["context/Dockerfile"] == dockerfile(lab, receipt["prepared_sha256"]), "identity")
    return receipt, files, lab


def validate_build_record(raw, expected, receipt, preparation_sha, contract):
    """Host-owner handoff: callers first authenticate preparation via validated_files.

    No current-HEAD equality: a later reviewed host owner may consume an older
    exact approved image. Runtime identity still requires a fresh daemon inspect.
    """
    require(type(expected) is str and re.fullmatch("[0-9a-f]{64}", expected)
            and len(raw) <= 16384 and digest(raw) == expected, "identity")
    report = owner.service.correctness.strict_json(raw)
    require(type(report) is dict and set(report) == {"schema", "result", "preparation_sha256",
        "prepared_sha256", "base_binding", "image_id", "image_built", "build_completed", "runtime_started",
        "hardware_validated", "deployable", "published"} and report["schema"] == BUILD_SCHEMA
        and report["result"] == "pass" and report["preparation_sha256"] == preparation_sha
        and report["prepared_sha256"] == receipt["prepared_sha256"]
        and report["image_built"] is True and report["build_completed"] is True
        and all(report[key] is False for key in ("runtime_started", "hardware_validated", "deployable", "published"))
        and type(report["image_id"]) is str and re.fullmatch("sha256:[0-9a-f]{64}", report["image_id"]), "identity")
    binding = report["base_binding"]
    require(type(binding) is dict and type(binding.get("docker_reported_id")) is str
            and re.fullmatch("sha256:[0-9a-f]{64}", binding["docker_reported_id"])
            and encoded(binding) == encoded({
                "manifest_sha256": contract["base_image"].rsplit("sha256:", 1)[1],
                "raw_config_sha256": contract["official_config"]["sha256"],
                "docker_reported_id": binding["docker_reported_id"], "platform": "linux/amd64",
                "binding": "local-manifest-reference-platform-and-diffids", "raw_config_link_verified": True,
                "local_config_bytes_rehashed": False, "local_layers_rehashed": False,
                "daemon_os_interpreter_trusted": True}), "identity")
    return report


def build(path, expected, output, docker_host):
    receipt, files, contract = validated_files(Path(path), expected)
    lab = host_recorder()
    # Same reviewed raw-OCI/platform/DiffID checker and bounded Docker process
    # implementation as the lab, loaded from this checkout, never the image.
    builder = lab.load("selected_image_application", ROOT / lab.BUILDER, lab.BUILDER_SHA)
    recorder = builder.recorder
    revision, sources = committed_sources(recorder)
    require(revision == receipt["git_sha"] and all(files.get("context/service/" + name) == raw
        for name, raw in sources.items() if name.startswith("scripts/")), "identity")
    started, finished = False, False
    try:
        with tempfile.TemporaryDirectory(prefix="nib-selected-service-build-") as temporary:
            work = Path(temporary).resolve()
            env = lab.controlled_env(docker_host)
            env.update(DOCKER_CONFIG=str(work / "docker"), BUILDX_CONFIG=str(work / "docker/buildx"),
                       DOCKER_BUILDKIT="1")
            recorder.prep.publish(work / "docker", {})
            recorder.snapshot.require_buildkit(env)
            recorder.prep.publish(work / "metadata", {name.removeprefix("metadata/"): raw
                for name, raw in files.items() if name.startswith("metadata/")})
            config = recorder.raw_metadata(work / "metadata", contract)
            base = recorder.docker_json("image", contract["base_image"], env, recorder.snapshot.execute_process_group)
            binding = recorder.image_binding(base, config, contract)
            context = {name.removeprefix("context/"): raw for name, raw in files.items()
                       if name.startswith("context/")}
            recorder.prep.publish(work / "context", context)
            image_file = work / "image-id"
            started = True
            recorder.call(["docker", "build", "--pull=false", "--network=none", "--no-cache",
                "--platform", "linux/amd64", "--iidfile", str(image_file), str(work / "context")],
                env, timeout=1800, limit=2 << 20)
            finished = True
            image_id = read(str(image_file), 80).decode().strip()
            require(re.fullmatch("sha256:[0-9a-f]{64}", image_id), "identity")
            image = recorder.docker_json("image", image_id, env, recorder.snapshot.execute_process_group)
            command = [contract["interpreter"]["resolved"], "-I", "-S", "-B",
                "/opt/nib-service/scripts/selected_service_process.py", "--prepared-sha256", receipt["prepared_sha256"]]
            require(image["Id"] == image_id and image["Os"] == "linux" and image["Architecture"] == "amd64"
                    and image["Config"]["User"] == "65532:65532" and image["Config"]["Entrypoint"] == command
                    and not image["Config"].get("Cmd") and not image["Config"].get("Volumes")
                    and image["RootFS"]["Type"] == "layers"
                    and image["RootFS"]["Layers"][:len(config["rootfs"]["diff_ids"])] == config["rootfs"]["diff_ids"],
                    "identity")
            report = {"schema": BUILD_SCHEMA, "result": "pass",
                "preparation_sha256": expected, "prepared_sha256": receipt["prepared_sha256"],
                "base_binding": binding, "image_id": image_id, "image_built": True,
                "build_completed": True, "runtime_started": False, "hardware_validated": False,
                "deployable": False, "published": False}
            raw = encoded(report)
            validate_build_record(raw, digest(raw), receipt, expected, contract)
            recorder.prep.publish(Path(output), {"build.json": raw})
            return report
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, KeyboardInterrupt):
        # A stopped client does not establish daemon-side BuildKit completion.
        # No image deletion by broad tag/name and no fabricated cleanup proof.
        return {"schema": BUILD_SCHEMA, "result": "fail",
                "build_started": started, "build_completed": finished,
                "cleanup": "unknown" if started else "not-started", "runtime_started": False,
                "hardware_validated": False, "deployable": False, "published": False}


class QuietParser(argparse.ArgumentParser):
    def error(self, message):
        raise owner.service.Rejected("contract")


def main():
    parser = QuietParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_subparsers(dest="mode", required=True)
    preparation = modes.add_parser("prepare", allow_abbrev=False)
    for name in ("lab-bundle", "lab-receipt-sha256", "trcs", "output"):
        preparation.add_argument("--" + name, required=True)
    preparation.add_argument("--checker-wheelhouse")
    sealing = modes.add_parser("seal", allow_abbrev=False)
    sealing.add_argument("--prepared-sha256", required=True)
    building = modes.add_parser("build", allow_abbrev=False)
    for name in ("preparation", "receipt-sha256", "output", "docker-host"):
        building.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if args.mode == "seal":
        seal(args.prepared_sha256)
    elif args.mode == "build":
        report = build(args.preparation, args.receipt_sha256, args.output, args.docker_host)
        print(encoded(report).decode())
        if report["result"] != "pass":
            raise SystemExit(1)
    else:
        print(encoded(prepare(args.lab_bundle, args.lab_receipt_sha256, args.trcs, args.output,
                             checker_wheelhouse=args.checker_wheelhouse)).decode())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Selected service image preparation rejected; no deployment approval.")
