#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record a bounded read-only diagnostic of one already-cached public image."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site):
    sys.exit("Use python3 -I -S scripts/record-engine-image.py")

import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import types
import uuid

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/record-engine-image.py"
PROBE = "scripts/engine-image-probe.py"
DIAGNOSTIC = "scripts/engine_image_diagnostic.py"
SNAPSHOT = "scripts/e2e/snapshot.py"
TEST = "scripts/test_engine_image.py"
CENSUS = "evidence/p0/2026-10-19-official-vllm-metadata-census/output.log"
MAX_REPORT = 2 << 20
GO_TOOLCHAIN = "go1.27.2"
PREPARER_SHA = "1c63b72c522b14edafc53311f736b8f237916d0a91faccca8be9909096ee1d6b"
SNAPSHOT_SHA = "b7f5753ecacde3cda2bf5fdc673b6f2af239875f2a9468e34a5219a797b78b3a"
DIAGNOSTIC_SHA = "e97285260e589d4d7cb6405529275a49a88be72778977fb34f03394dea2b2a48"
CANCELLED = False


def cancel(signum, frame):
    global CANCELLED
    if not CANCELLED:
        CANCELLED = True
        raise InterruptedError("diagnostic cancelled")


def bootstrap_read(path):
    # Self-contained first-helper trust boundary; subsequent reads reuse prep.
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("source rejected")
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_REPORT:
                raise ValueError("source rejected")
            data = stream.read(MAX_REPORT + 1)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                        info.st_mtime_ns, info.st_ctime_ns)
            if (len(data) != before.st_size or len(data) > MAX_REPORT
                    or identity(before) != identity(os.fstat(stream.fileno()))
                    or identity(before) != identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False))):
                raise ValueError("source changed")
            return data
    finally:
        os.close(parent)


def load(name, path, expected, reader=bootstrap_read):
    data = reader(path)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("helper source differs")
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module, data


try:
    BOOTSTRAP = bootstrap_read(Path(__file__).absolute())
    prep, PREPARER_SOURCE = load("image_preparer", ROOT / "scripts/prepare-engine-overlay.py", PREPARER_SHA)
    snapshot, SNAPSHOT_SOURCE = load("image_processes", ROOT / SNAPSHOT, SNAPSHOT_SHA, prep.read_source)
    diagnostic, DIAGNOSTIC_SOURCE = load("image_observations", ROOT / DIAGNOSTIC, DIAGNOSTIC_SHA, prep.read_source)
except Exception:
    if __name__ == "__main__":
        sys.exit("Image diagnostic bootstrap rejected.")
    raise

overlay, checker = prep.inspector, prep.checker


def inputs(root=ROOT):
    revision, bundle, extra = prep.inputs(root)
    for name in (SCRIPT, PROBE, DIAGNOSTIC, SNAPSHOT, TEST, CENSUS):
        entry = checker.git(root, "ls-tree", revision, "--", name).decode().split()
        overlay.require(len(entry) == 4 and entry[:2] == ["100644", "blob"] and entry[3] == name)
        overlay.require(int(checker.git(root, "cat-file", "-s", entry[2])) <= checker.LIMIT)
        extra[name] = checker.git(root, "cat-file", "blob", entry[2])
    overlay.require(extra[SCRIPT] == BOOTSTRAP and extra[DIAGNOSTIC] == DIAGNOSTIC_SOURCE
                    and extra[SNAPSHOT] == SNAPSHOT_SOURCE and extra[prep.SCRIPT] == PREPARER_SOURCE)
    return revision, bundle, extra


def payload_files(revision, bundle, extra, *, mode="integrity"):
    # Reuse exact candidate validation/application in memory, never apply to an image.
    prepared, _ = prep.prepare_files(revision, bundle, extra)
    contract = overlay.load_json(prepared["payload/contract.json"])
    overlay.require(mode in {"integrity", "metadata-census"})
    contract["diagnostic_mode"] = mode
    if mode == "integrity":
        policy = overlay.DIAGNOSTIC_INVENTORY_POLICY
        overlay.require(contract["base_image"] == policy["base_image"]
                        and contract["official_config"]["sha256"] == policy["config_sha256"]
                        and overlay.digest(extra[CENSUS]) == policy["census_output_sha256"])
        contract["diagnostic_inventory_policy"] = dict(policy)
        nonhost = overlay.CUTLASS_STUB_POLICY
        overlay.require(contract["base_image"] == nonhost["base_image"]
                        and contract["official_config"]["sha256"] == nonhost["config_sha256"])
        contract["diagnostic_nonhost_policy"] = nonhost
        contract["diagnostic_integrity_details"] = True
    files = {"engine_overlay.py": extra[prep.INSPECTOR], "engine_image_diagnostic.py": extra[DIAGNOSTIC],
             "probe.py": extra[PROBE]}
    contract["payload_sources"] = {name: overlay.digest(data) for name, data in files.items()}
    files["contract.json"] = overlay.json_bytes(contract)
    return files, contract


def raw_metadata(directory, pins):
    with overlay.directory(directory) as fd:
        tree = overlay.Tree(fd)
        with os.scandir(fd) as entries:
            names = set()
            for entry in entries:
                overlay.require(len(names) < 2)
                names.add(entry.name)
            overlay.require(names == {"manifest.json", "config.json"})
        manifest_data, config_data = tree.read("manifest.json"), tree.read("config.json")
    manifest_sha = pins["base_image"].rsplit("sha256:", 1)[1]
    overlay.require(overlay.digest(manifest_data) == manifest_sha
                    and overlay.digest(config_data) == pins["official_config"]["sha256"]
                    and len(config_data) == pins["official_config"]["bytes"])
    manifest, config = overlay.load_json(manifest_data), overlay.load_json(config_data)
    overlay.require(manifest["schemaVersion"] == 2 and manifest["mediaType"] in {
        "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"})
    descriptor = manifest["config"]
    overlay.require(descriptor["digest"] == "sha256:" + pins["official_config"]["sha256"]
                    and descriptor["size"] == len(config_data) and descriptor["mediaType"] in {
                        "application/vnd.oci.image.config.v1+json", "application/vnd.docker.container.image.v1+json"})
    overlay.require(config["os"] == "linux" and config["architecture"] == "amd64"
                    and config["rootfs"]["type"] == "layers" and not config["config"].get("Volumes"))
    layers = config["rootfs"]["diff_ids"]
    overlay.require(0 < len(layers) == len(manifest["layers"]) <= 128
                    and all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in layers))
    return config


def image_binding(value, config, pins):
    overlay.require(value["Os"] == "linux" and value["Architecture"] == "amd64"
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", value["Id"]))
    expected = pins["base_image"].rsplit("@", 1)[1]
    descriptor = value.get("Descriptor") or {}
    repo_match = pins["base_image"] in (value.get("RepoDigests") or [])
    descriptor_match = descriptor.get("digest") == expected
    overlay.require(repo_match or descriptor_match)
    if descriptor:
        overlay.require(descriptor_match)
    overlay.require(value["RootFS"]["Type"] == "layers"
                    and value["RootFS"]["Layers"] == config["rootfs"]["diff_ids"])
    return {"manifest_sha256": expected[7:], "raw_config_sha256": pins["official_config"]["sha256"],
            "docker_reported_id": value["Id"], "platform": "linux/amd64",
            "binding": "local-manifest-reference-platform-and-diffids",
            "raw_config_link_verified": True, "local_config_bytes_rehashed": False,
            "local_layers_rehashed": False, "daemon_os_interpreter_trusted": True}


def call(args, env, *, execute=snapshot.execute_process_group, timeout=30, limit=MAX_REPORT):
    result = execute(args, env=env, timeout=timeout, max_output_bytes=limit)
    result.check_returncode()
    overlay.require(len(result.stdout) <= limit and len(result.stderr) <= 65536)
    return result.stdout


def docker_json(kind, name, env, execute):
    data = call(["docker", kind, "inspect", "--format", "{{json .}}", name], env, execute=execute)
    value = overlay.load_json(data)
    overlay.require(isinstance(value, dict))
    return value


def create_command(name, owner, payload, contract_sha, config):
    args = ["docker", "container", "create", "--name", name, "--label", "nib.diagnostic=" + owner,
            "--pull", "never", "--platform", "linux/amd64", "--runtime", "runc", "--network", "none",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "65532:65532",
            "--pids-limit", "32", "--memory", "1g", "--memory-swap", "1g", "--cpus", "2",
            "--ipc", "none", "--cgroupns", "private", "--no-healthcheck", "--restart", "no",
            "--log-driver", "none", "--workdir", "/", "--entrypoint", "python3",
            "--mount", "type=bind,src=" + str(payload) + ",dst=/opt/nib,readonly"]
    # Clear every inherited image environment value, not just known Python hooks.
    names = set()
    for entry in config["config"].get("Env", []):
        name, separator, _ = entry.partition("=")
        overlay.require(separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) and name not in names)
        names.add(name)
    names |= {"PATH", "HOME"}
    for name in sorted(names):
        value = "/usr/local/bin:/usr/bin:/bin" if name == "PATH" else "/nonexistent" if name == "HOME" else ""
        args += ["--env", name + "=" + value]
    return args + [overlay.BASE_IMAGE, "-I", "-S", "-B", "/opt/nib/probe.py", contract_sha]


def check_container(value, owner, payload, contract_sha, image_id, expected_env):
    config, host = value["Config"], value["HostConfig"]
    overlay.require(config["Labels"].get("nib.diagnostic") == owner and value["Image"] == image_id
        and config["Image"] == overlay.BASE_IMAGE and config["Entrypoint"] == ["python3"]
        and config["Cmd"] == ["-I", "-S", "-B", "/opt/nib/probe.py", contract_sha]
        and config["User"] == "65532:65532" and config["WorkingDir"] == "/"
        and config["Healthcheck"]["Test"] == ["NONE"] and not config.get("Tty") and not config.get("OpenStdin"))
    overlay.require(len(config["Env"]) == len(expected_env) and set(config["Env"]) == set(expected_env))
    overlay.require(host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
        and host["Privileged"] is False and host["Runtime"] == "runc" and host["CapDrop"] == ["ALL"]
        and not host.get("CapAdd") and not host.get("Devices") and not host.get("DeviceRequests")
        and not host.get("DeviceCgroupRules") and not host.get("PortBindings") and not host.get("PublishAllPorts")
        and not host.get("PidMode") and not host.get("UTSMode") and not host.get("VolumesFrom")
        and not host.get("Binds") and not host.get("Tmpfs") and host["IpcMode"] == "none"
        and host["CgroupnsMode"] == "private" and host["Memory"] == host["MemorySwap"] == 1 << 30
        and host["NanoCpus"] == 2000000000 and host["PidsLimit"] == 32
        and host["RestartPolicy"]["Name"] == "no" and host["LogConfig"]["Type"] == "none"
        and set(host["SecurityOpt"]) in ({"no-new-privileges"}, {"no-new-privileges=true"}))
    mounts = value["Mounts"]
    overlay.require(len(mounts) == 1 and mounts[0]["Type"] == "bind" and mounts[0]["Source"] == str(payload)
                    and mounts[0]["Destination"] == "/opt/nib" and mounts[0]["RW"] is False)


def run_container(payload, contract, config, env, execute=snapshot.execute_process_group):
    name, owner = "nib-image-diagnostic-" + uuid.uuid4().hex, uuid.uuid4().hex
    report = {"result": "fail", "stage": "image-preflight", "cleanup": "not-created",
              "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False, "hardware_validated": False}
    create_started = create_complete = runtime_complete = False
    absent = ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"]
    contract_sha = overlay.digest(prep.read_source(payload / "contract.json"))
    try:
        value = docker_json("image", overlay.BASE_IMAGE, env, execute)
        report["image"] = image_binding(value, config, contract)
        overlay.require(not call(absent, env, execute=execute).strip())
        command = create_command(name, owner, payload, contract_sha, config)
        report["stage"] = "container-create"
        create_started = True
        created = call(command, env, execute=execute).decode().strip()
        overlay.require(re.fullmatch(r"[0-9a-f]{64}", created))
        create_complete = True
        report["stage"] = "isolation-check"
        info = docker_json("container", name, env, execute)
        expected_env = [command[i + 1] for i, value in enumerate(command) if value == "--env"]
        check_container(info, owner, payload, contract_sha, value["Id"], expected_env)
        overlay.require(info["Id"] == created and info["State"]["Status"] == "created")
        report["stage"] = "read-only-diagnostic"
        limit = diagnostic.CENSUS_LIMITS["report_bytes"] if contract.get("diagnostic_mode") == "metadata-census" else MAX_REPORT
        data = call(["docker", "container", "start", "--attach", name], env, execute=execute, timeout=600, limit=limit)
        result = overlay.load_json(data)
        diagnostic.validate_report(overlay, result, contract)
        terminal = docker_json("container", name, env, execute)
        overlay.require(terminal["Id"] == created and terminal["State"]["Status"] == "exited"
                        and terminal["State"]["ExitCode"] == 0 and not terminal["State"].get("OOMKilled"))
        runtime_complete = True
        report["diagnostic"] = result
        report["result"] = "pass" if result["overlay_admission"] == "accepted" else "fail"
        report["stage"] = "complete" if report["result"] == "pass" else "overlay-rejected"
        if contract.get("diagnostic_mode") == "metadata-census":
            report["stage"] = "metadata-census-complete" if result["collection"] == "pass" else "metadata-census-partial"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        report["failure"] = "diagnostic-stage-failed"
    finally:
        if create_started:
            report["cleanup"] = "unknown"
            try:
                if call(absent, env, execute=execute).strip():
                    owned = docker_json("container", name, env, execute)
                    overlay.require(owned["Config"]["Labels"].get("nib.diagnostic") == owner)
                    call(["docker", "container", "rm", "--force", name], env, execute=execute)
                if not call(absent, env, execute=execute).strip():
                    report["cleanup"] = "confirmed"
            except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
                pass
            if not create_complete or (report["stage"] == "read-only-diagnostic" and not runtime_complete):
                report["cleanup"] = "completion-unknown"
            if report["cleanup"] != "confirmed":
                report.update(result="fail", failure="cleanup-unresolved")
    return report


def offline_environment(control):
    env = snapshot.controlled_env(control)
    env.update(checker.tool_env(), GOPROXY="off", GOSUMDB="off", GOTOOLCHAIN=GO_TOOLCHAIN)
    env["HOME"] = str(control / "home")
    # Go 1.24.2/1.27.2 cmd/internal/telemetry and x/telemetry/start.go:
    # isolate counters and suppress detached sidecars before even `go env`.
    telemetry = control / "telemetry"
    prep.publish(telemetry, {"mode": b"off\n"})
    env.update(TEST_TELEMETRY_DIR=str(telemetry), GO_TELEMETRY_CHILD="2")
    for key in ("GOCACHE", "GOMODCACHE"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def validator_toolchain(env):
    # The older bootstrap's updater re-verifies even cached toolchains via
    # GOSUMDB. Query local metadata only, then invoke the pinned cache directly.
    selected = shutil.which("go", path=os.environ.get("PATH", os.defpath))
    overlay.require(selected is not None and Path(selected).is_absolute())
    local = {**env, "GOTOOLCHAIN": "local", "HOME": pwd.getpwuid(os.getuid()).pw_dir}
    # Discover host caches, while retaining the private, disabled telemetry
    # controls. Private GOPATH/HOME would select empty temporary caches here.
    local.pop("GOPATH", None)
    for key in ("GOCACHE", "GOMODCACHE"):
        if key not in os.environ:
            local.pop(key, None)
    info = overlay.load_json(call([selected, "env", "-json", "GOVERSION", "GOHOSTOS", "GOHOSTARCH", "GOMODCACHE", "GOCACHE"],
                                  local, timeout=10, limit=4096))
    qualified = dict(env)
    for key in ("GOCACHE", "GOMODCACHE"):
        cache = Path(info[key])
        overlay.require(cache.is_absolute() and ".." not in cache.parts)
        qualified[key] = str(cache)
    if info["GOVERSION"] == GO_TOOLCHAIN:
        return selected, qualified
    overlay.require(all(re.fullmatch(r"[a-z0-9]{1,16}", info[key]) for key in ("GOHOSTOS", "GOHOSTARCH")))
    # Go cmd/go/internal/toolchain/select.go's public toolchain module layout.
    module = "golang.org/toolchain@v0.0.1-" + GO_TOOLCHAIN + "." + info["GOHOSTOS"] + "-" + info["GOHOSTARCH"]
    binary = str(Path(qualified["GOMODCACHE"]) / module / "bin/go")
    version = call([binary, "version"], local, timeout=10, limit=4096)
    overlay.require(version == ("go version " + GO_TOOLCHAIN + " " + info["GOHOSTOS"] + "/" + info["GOHOSTARCH"] + "\n").encode())
    return binary, qualified


def build_validator(root, revision, work, env):
    go, env = validator_toolchain(env)
    source = work / "validator-source"
    source.mkdir()
    entries = checker.git(root, "ls-tree", "-r", "-z", revision, "--", "go.mod", "go.sum", "cmd/nib", "internal", "schemas")
    total, files = 0, {}
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.decode().split("\t")
        mode, kind, oid = meta.split()
        overlay.safe_name(name)
        overlay.require(mode in {"100644", "100755"} and kind == "blob" and len(files) < 4096
                        and int(checker.git(root, "cat-file", "-s", oid)) <= checker.LIMIT)
        data = checker.git(root, "cat-file", "blob", oid)
        total += len(data)
        overlay.require(total <= 32 << 20)
        files[name] = data
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    binary = work / "nib"
    call([go, "build", "-trimpath", "-buildvcs=false", "-o", str(binary), "./cmd/nib"], env,
         execute=lambda args, **kw: snapshot.execute_process_group(args, cwd=source, **kw), timeout=180)
    return binary, overlay.bundle_digest(files)


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_evidence(record, report, nib, work, destination, env):
    validation = work / "validation/evidence/p0/candidate"
    validation.mkdir(parents=True)
    (validation / "run.json").write_bytes(overlay.json_bytes(record))
    call([str(nib), "lint", str(work / "validation")], env, timeout=20)
    prep.publish(destination, {"run.json": overlay.json_bytes(record), "output.log": overlay.json_bytes(report)})


def evidence_record(revision, slug, started, report, *, mode="integrity"):
    overlay.require(mode in {"integrity", "metadata-census"})
    census = mode == "metadata-census"
    overlay.require(not census or report["result"] == "fail")
    return {"schema": "nib.evidence/v1", "phase": "P0", "task": "T4", "git_sha": revision,
            "command": "python3 -I -S scripts/record-engine-image.py " + ("--metadata-census " if census else "") + "--slug " + slug,
            "started_utc": started, "ended_utc": now(), "result": report["result"], "execution": "software",
            "hardware_validated": False, "host": {"provider": "local", "machine": "local-software-diagnostic",
                "gpus": "none", "driver": None, "cuda": None},
            "notes": "Read-only software diagnostic, not ABI/hardware/confidential qualification. "
                     + ("Metadata-only census does not evaluate admission; the run stays failed even if counting completes. "
                        if census else "Overlay rejection is a failed run even when collection completes. ")
                     + "Docker/OS/interpreter trusted. "
                     "No pull, build, image deletion, engine import or application. Payload SHA-256: "
                     + report["provenance"]["payload_sha256"]}


def main():
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    parser = overlay.QuietParser(description=__doc__)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--metadata-census", action="store_true", help="Count metadata only; never evaluate admission.")
    args = parser.parse_args()
    overlay.require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.slug) and len(args.slug) <= 64)
    revision, bundle, extra = inputs()
    mode = "metadata-census" if args.metadata_census else "integrity"
    files, contract = payload_files(revision, bundle, extra, mode=mode)
    started = now()
    destination = ROOT / "evidence/p0" / (started[:10] + "-" + args.slug)
    overlay.require(not os.path.lexists(destination))
    with tempfile.TemporaryDirectory(prefix="nib-image-diagnostic-", dir="/private/tmp" if sys.platform == "darwin" else None) as temporary:
        work = Path(temporary).resolve()
        env = offline_environment(work / "control")
        # Cache-only authoritative validator, built from the same committed source.
        nib, validator_sha = build_validator(ROOT, revision, work, env)
        report = {"result": "fail", "stage": "raw-oci-metadata", "cleanup": "not-created",
                  "candidate_status": "BLOCKED", "deployable": False, "abi_validated": False, "hardware_validated": False}
        raw_verified = False
        try:
            config = raw_metadata(ROOT / "build/engine-image-metadata", contract)
            raw_verified = True
            report["stage"] = "local-docker-endpoint"
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            discovery = {**env, "HOME": str(home), "DOCKER_CONFIG": str(home / ".docker")}
            endpoint = call(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                            discovery, timeout=10, limit=4096).decode().strip()
            overlay.require(endpoint.startswith("unix:///") and not any(ord(c) < 32 for c in endpoint))
            env["DOCKER_HOST"] = endpoint
            payload = work / "payload"
            prep.publish(payload, files)
            # The enclosing temporary directory stays 0700; only this read-only
            # bind mount is readable by the unprivileged container UID.
            for name in files:
                (payload / name).chmod(0o444)
            payload.chmod(0o555)
            overlay.require({name: prep.read_source(payload / name) for name in files} == files)
            report = run_container(payload, contract, config, env)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
            report["failure"] = "preflight-failed"
        report["provenance"] = {"git_sha": revision, "source_bundle_sha256": contract["source_bundle_sha256"],
                                "payload_sha256": overlay.bundle_digest(files), "validator_source_sha256": validator_sha,
                                "requested_image": overlay.BASE_IMAGE, "expected_config_sha256": contract["official_config"]["sha256"],
                                "raw_oci_metadata_verified": raw_verified, "diagnostic_mode": mode}
        record = evidence_record(revision, args.slug, started, report, mode=mode)
        publish_evidence(record, report, nib, work, destination, env)
    print(destination.relative_to(ROOT).as_posix())
    return 0 if record["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit("Image diagnostic recording failed; no successful evidence may be inferred.")
