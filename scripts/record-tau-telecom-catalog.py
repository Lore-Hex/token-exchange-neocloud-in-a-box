#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Reproduce public tool schemas in the pinned, network-disabled tau runtime."""

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import tempfile
import time
import types
import uuid

SCRIPT = "scripts/record-tau-telecom-catalog.py"
SECONDS = 180


def require(value):
    if not value:
        raise ValueError("telecom catalog rejected")


def load(path, sha=None):
    raw = path.read_bytes()
    require(len(raw) <= 1 << 20 and (sha is None or hashlib.sha256(raw).hexdigest() == sha))
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def materialize():
    from tau2.domains.telecom.environment import get_environment, get_tasks
    env = get_environment()
    tools = [tool.openai_schema for tool in env.get_user_tools()]
    encoded = lambda value: (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    digest = lambda value: hashlib.sha256(encoded(value)).hexdigest()
    require(len(tools) == 30 and len({tool["function"]["name"] for tool in tools}) == 30)
    tasks = sorted(get_tasks(task_split_name="base"), key=lambda task: task.id)
    selected = {}
    for task in tasks[:5]:
        # Same filtering and order as runner.build.build_user. Do not silently
        # substitute the unfiltered catalog if upstream selection is different.
        actual = [tool.openai_schema for tool in env.get_user_tools(include=task.user_tools)]
        require(actual == tools)
        selected[task.id] = {"tool_count": len(actual), "tool_catalog_sha256": digest(actual),
                             "user_tools": task.user_tools}
    return {"tools": tools, "tool_catalog_sha256": digest(tools), "tool_count": len(tools),
            "base_task_count": len(tasks), "selected_tasks": selected}


def child(plan_sha):
    require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
            and sys.version_info[:3] == (3, 12, 13) and sys.platform == "linux"
            and os.uname().machine == "x86_64" and os.confstr("CS_GNU_LIBC_VERSION") == "glibc 2.36")
    root = Path("/opt/nib")
    raw = (root / "trial.json").read_bytes()
    require(len(raw) <= 4 << 20 and hashlib.sha256(raw).hexdigest() == plan_sha)
    plan = json.loads(raw)
    worker = load(root / "scripts/tau_trial_worker.py", plan["files"]["scripts/tau_trial_worker.py"]["sha256"])
    with worker.alarm(time.monotonic() + SECONDS):
        worker.verify_tree(root, plan["files"], time.monotonic() + 60)
        sys.path[:] = [p for p in sys.path if p and "site-packages" not in p]
        sys.path.extend([str(root / "upstream/src"), str(root / "packages")])
        sys.addaudithook(worker.deny_external)
        # Only public source-derived schemas escape; upstream logs and exception
        # text never do. No orchestrator step, tool execution, or model call.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            import logging
            logging.disable(logging.CRITICAL)
            from loguru import logger
            logger.remove()
            result = materialize()
        result.update(plan["source"])
        raw = worker.encoded(result)
        require(len(raw) <= 65536)
        sys.stdout.buffer.write(raw)


def prepare(trial, archive, wheelhouse, deadline):
    r = trial.runtime
    pins_raw = r.read_file(trial.ROOT / r.tau.PINS, 1 << 20, deadline)
    require(r.digest(pins_raw) == r.tau.PINS_SHA)
    lock_raw = r.read_file(trial.ROOT / r.CATALOG, 1 << 20, deadline)
    require(r.digest(lock_raw) == r.CATALOG_SHA)
    lock = r.tau.json_value(lock_raw)
    rows, sources = r.source_inputs(r.read_file(archive, r.ARCHIVE_LIMIT, deadline), r.tau.json_value(pins_raw), deadline)
    require(r.make_catalog(rows, sources) == lock)
    files = {"upstream/" + name: raw for name, raw in sources.items()}
    owners = {}
    for package, row in lock["wheels"].items():
        trial.add_wheel(files, owners, r.read_file(wheelhouse / row["filename"], r.WHEEL_LIMIT, deadline),
                        package, row, deadline)
    for name in (SCRIPT, "scripts/tau_trial_worker.py"):
        files[name] = r.read_file(trial.ROOT / name, 1 << 20, deadline)
    plan = {"files": {name: {"sha256": r.digest(raw), "bytes": len(raw)} for name, raw in files.items()},
            "source": {"schema": "nib.tau-telecom-source-catalog/v1", "upstream_revision": r.REVISION,
                       "source_archive_sha256": r.ARCHIVE_SHA, "source_tree": r.SOURCE_TREE,
                       "runtime_lock_sha256": r.CATALOG_SHA, "target": r.TARGET,
                       "license": "MIT", "copyright": "Copyright Sierra"}}
    files["trial.json"] = r.encoded(plan)
    return files, r.digest(files["trial.json"])


def command(trial, name, payload, sha):
    result = trial.create_command(name, name, payload, sha)
    require(result[-2:] == ["/opt/nib/" + trial.WORKER, sha])
    result[-2:] = ["/opt/nib/" + SCRIPT, "--worker", sha]
    return result


def check_container(trial, info, name, payload, sha, selected_manifest):
    actual = command(trial, name, payload, sha)
    require(info["Config"]["Cmd"] == actual[actual.index(trial.runtime.TARGET["image"]) + 1:])
    # This job differs only in its verified entrypoint/arguments. Compare those
    # above, then reuse every existing container isolation and image check.
    baseline = trial.create_command(name, name, payload, sha)
    normalized = json.loads(json.dumps(info))
    normalized["Config"]["Cmd"] = baseline[baseline.index(trial.runtime.TARGET["image"]) + 1:]
    trial.check_container(normalized, name, name, payload, sha, selected_manifest)


def record(archive, wheelhouse, socket, output):
    root = Path(__file__).absolute().parent.parent
    trial = load(root / "scripts/tau_trial.py")
    require(socket.is_absolute() and stat.S_ISSOCK(socket.stat().st_mode))
    require(output.is_absolute() and not output.exists())
    executable = shutil.which("docker")
    require(executable is not None)
    deadline = time.monotonic() + SECONDS
    files, sha = prepare(trial, archive, wheelhouse, deadline)
    name = "nib-telecom-catalog-" + uuid.uuid4().hex
    # Local reproduction only, with the existing exact image and resource caps.
    # No pull, credentials, network, writable source, or live owner is introduced.
    with tempfile.TemporaryDirectory(prefix="nib-telecom-catalog-", dir=Path(tempfile.gettempdir()).resolve()) as directory:
        private = Path(directory) / "docker"
        private.mkdir()
        (private / "config.json").write_text('{"auths":{}}\n')
        prefix = [executable, "--host", "unix://" + str(socket), "--config", str(private)]
        env = {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOME": str(private),
               "DOCKER_CONFIG": str(private), "LANG": "C", "LC_ALL": "C"}
        destination = Path(directory) / "inputs"
        trial.publish(destination, files)
        payload = destination / "payload"
        def docker(args, limit=65536, remaining=None):
            available = deadline - time.monotonic()
            require(available > 0)
            return trial.probe.capture([*prefix, *args],
                timeout=min(30 if remaining is None else remaining, available),
                limit=limit, env=env)
        created = False
        try:
            selected_manifest = trial.check_image(json.loads(docker([
                "image", "inspect", "--platform", "linux/amd64", trial.runtime.TARGET["image"]], limit=1 << 20))[0])
            argv = command(trial, name, payload, sha)
            created = True  # Clean the uniquely owned name even after an ambiguous create.
            docker(argv)
            info = json.loads(docker(["inspect", name], limit=1 << 20))[0]
            check_container(trial, info, name, payload, sha, selected_manifest)
            raw = docker(["start", "--attach", name], remaining=SECONDS)
            final = json.loads(docker(["inspect", name], limit=1 << 20))[0]
            check_container(trial, final, name, payload, sha, selected_manifest)
            require(final["State"]["Running"] is False and final["State"]["ExitCode"] == 0
                    and final["State"]["OOMKilled"] is False)
            value = trial.tau.json_value(raw)
            require(value["upstream_revision"] == trial.runtime.REVISION and value["tool_count"] == 30)
            raw = trial.tau.encoded(value)
        finally:
            if created:
                # One owned local container only, never a blanket prune.
                trial.probe.capture([*prefix, "rm", "--force", name], timeout=15, env=env)
        with output.open("xb") as stream:
            stream.write(raw)
        return {"catalog_sha256": trial.tau.digest(raw), "tool_catalog_sha256": value["tool_catalog_sha256"],
                "tool_count": 30, "selected_tasks": len(value["selected_tasks"]), "source_authenticated": True,
                "token_count_parity_verified": False, "live_admission": False, "quality_eligible": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker")
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--docker-socket", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    previous = {kind: signal.getsignal(kind) for kind in (signal.SIGTERM, signal.SIGINT)}
    cancellation_requested = False
    def cancelled(signum, frame):
        nonlocal cancellation_requested
        if cancellation_requested:
            return
        cancellation_requested = True
        raise KeyboardInterrupt
    for kind in previous:
        signal.signal(kind, cancelled)
    try:
        require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
        if args.worker:
            require(args.source_archive is args.wheelhouse is args.docker_socket is args.output is args.receipt is None)
            child(args.worker)
        else:
            require(all(path is not None and path.is_absolute() for path in
                        (args.source_archive, args.wheelhouse, args.docker_socket, args.output)))
            require(args.receipt is None or args.receipt.is_absolute() and not args.receipt.exists()
                    and args.receipt != args.output)
            root = Path(__file__).absolute().parent.parent
            trial = load(root / "scripts/tau_trial.py")
            git = shutil.which("git")
            require(git is not None)
            revision = trial.probe.capture([git, "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
            dirty = bool(trial.probe.capture([git, "-C", str(root), "status", "--porcelain"]))
            started = datetime.now(timezone.utc).isoformat()
            result = record(args.source_archive, args.wheelhouse, args.docker_socket, args.output)
            result.update(schema="nib.tau-telecom-catalog-extraction/v1", result="pass",
                          producer_git_sha=revision, producer_dirty=dirty,
                          recorder_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          started_utc=started, ended_utc=datetime.now(timezone.utc).isoformat(),
                          upstream_revision=trial.runtime.REVISION, source_archive_sha256=trial.runtime.ARCHIVE_SHA,
                          source_tree=trial.runtime.SOURCE_TREE, runtime_lock_sha256=trial.runtime.CATALOG_SHA,
                          runtime_target=trial.runtime.TARGET, generation_calls=0, benchmark_executed=False,
                          cleanup="owned-container-removed",
                          command_line="python3 -I -S -B scripts/record-tau-telecom-catalog.py "
                          "--source-archive PRIVATE --wheelhouse PRIVATE --docker-socket PRIVATE "
                          "--output PRIVATE --receipt PRIVATE")
            if args.receipt is not None:
                with args.receipt.open("xb") as stream:
                    stream.write(trial.tau.encoded(result))
            print(json.dumps(result, sort_keys=True))
    except BaseException:
        print("telecom catalog rejected", file=sys.stderr)
        return 1
    finally:
        for kind, handler in previous.items():
            signal.signal(kind, handler)
    return 0


if __name__ == "__main__":
    sys.exit(main())
