# SPDX-License-Identifier: Apache-2.0
"""Private committed-source snapshots for the full E2E evidence recorder."""

import hashlib
import os
from pathlib import Path
import shutil
import signal
import selectors
import subprocess
import tarfile
import time


TOOL_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


def execute_process_group(args, *, env, cwd=None, timeout=1800, capture_output=True,
                          stdout_file=None, max_output_bytes=None, stdin=None):
    """Bound the entire local tool tree, including Docker CLI plugins."""
    stream = subprocess.PIPE if capture_output else subprocess.DEVNULL
    if ((stdout_file is not None and max_output_bytes is None)
            or (max_output_bytes is not None and (not capture_output or max_output_bytes <= 0))):
        raise ValueError("streaming output requires a byte limit")
    process = subprocess.Popen(args, env=env, cwd=cwd, start_new_session=True,
                               stdout=stream, stderr=stream, stdin=stdin)
    try:
        if max_output_bytes is None:
            stdout, stderr = process.communicate(timeout=timeout)
        else:
            stdout, stderr = bounded_output(process, args, timeout, stdout_file, max_output_bytes)
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    finally:
        cleanup_process_group(process)


def cleanup_process_group(process):
    """Always reap/close; denied signals do not replace proof of group absence."""
    try:
        reaped = False
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError):
                # A denied signal can race an exited child; reaping still matters.
                # Persistent denial or a surviving group fails the final query.
                pass
            try:
                process.wait(timeout=10)
                reaped = True
            except subprocess.TimeoutExpired:
                reaped = False
        if not reaped:
            raise ValueError("tool process-group absence unproven")
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()
    deadline = time.monotonic() + 5
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            raise ValueError("tool process-group absence unproven") from None
        if time.monotonic() >= deadline:
            raise ValueError("tool process-group absence unproven")
        time.sleep(0.05)


def bounded_output(process, args, timeout, output, limit):
    """Drain both pipes without retaining a Docker archive or unbounded diagnostics."""
    deadline = time.monotonic() + timeout
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    sizes = dict.fromkeys(buffers, 0)
    with selectors.DefaultSelector() as selector:
        for pipe in buffers:
            selector.register(pipe, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            for key, _ in selector.select(min(remaining, 0.1)):
                pipe = key.fileobj
                chunk = os.read(pipe.fileno(), 65536)
                if not chunk:
                    selector.unregister(pipe)
                    continue
                sizes[pipe] += len(chunk)
                if sizes[pipe] > (limit if pipe is process.stdout else 65536):
                    raise ValueError("tool output limit exceeded")
                if pipe is process.stdout and output is not None:
                    if output.write(chunk) != len(chunk):
                        raise ValueError("short tool output write")
                else:
                    buffers[pipe].extend(chunk)
        process.wait(timeout=max(0, deadline - time.monotonic()))
    return bytes(buffers[process.stdout]), bytes(buffers[process.stderr])


def controlled_env(control):
    for name in ("home", "tmp", "templates", "docker", "go", "go-cache", "go-mod"):
        (control / name).mkdir(parents=True, exist_ok=True)
    return {
        "PATH": TOOL_PATH, "HOME": str(control / "home"), "TMPDIR": str(control / "tmp"),
        "LANG": "C", "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TEMPLATE_DIR": str(control / "templates"),
        # Exact version matches committed go.mod and the pinned Go build image.
        "GOENV": "off", "GOWORK": "off", "GOTOOLCHAIN": "go1.27.2", "GOFLAGS": "-mod=readonly",
        "CGO_ENABLED": "0", "GOPATH": str(control / "go"),
        "GOCACHE": str(control / "go-cache"), "GOMODCACHE": str(control / "go-mod"),
        "GOPROXY": "https://proxy.golang.org", "GOSUMDB": "sum.golang.org",
        "DOCKER_CONFIG": str(control / "docker"), "BUILDX_CONFIG": str(control / "docker/buildx"),
        "DOCKER_BUILDKIT": "1",
    }


def git(root, env, *args, **options):
    return subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(root), *args],
                          env=env, check=True, timeout=60, stderr=subprocess.PIPE, **options)


def local_docker_endpoint(env, home):
    # Read only the normal user's selected context. Never inherit DOCKER_HOST,
    # DOCKER_CONFIG, TLS/proxy variables, or credentials into build children.
    discovery = {**env, "HOME": str(home), "DOCKER_CONFIG": str(home / ".docker")}
    result = subprocess.run(["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                            env=discovery, check=True, capture_output=True, timeout=10)
    endpoint = result.stdout.decode().strip()
    if not endpoint.startswith("unix:///") or "\n" in endpoint or "\r" in endpoint:
        raise ValueError("only a local Unix-socket Docker endpoint is allowed")
    return endpoint


def require_buildkit(env):
    # OrbStack installs Buildx next to its Docker executable, while Linux
    # packages normally use Docker's system plugin directories. Expose only
    # that installed executable, never the user's Docker config/credentials.
    docker = shutil.which("docker", path=env["PATH"])
    if docker is None:
        raise ValueError("Docker executable unavailable")
    sibling = Path(docker).resolve().with_name("docker-buildx")
    if sibling.is_file() and os.access(sibling, os.X_OK):
        plugins = Path(env["DOCKER_CONFIG"]) / "cli-plugins"
        plugins.mkdir()
        (plugins / "docker-buildx").symlink_to(sibling)
    subprocess.run(["docker", "buildx", "version"], env=env, check=True, capture_output=True, timeout=10)


def snapshot_commit(root, destination, control, env, revision=None):
    if revision is None:
        revision = git(root, env, "rev-parse", "--verify", "HEAD^{commit}", stdout=subprocess.PIPE).stdout.decode().strip()
    destination.mkdir()
    git(destination, env, "init", "--quiet", stdout=subprocess.PIPE)
    # Transfer objects, not a checkout or local configuration. No replacement
    # refs, alternates, hooks, ignored files, or source .git metadata are copied.
    pack = control / "source.pack"
    with pack.open("wb") as output:
        git(root, env, "pack-objects", "--revs", "--stdout", input=(revision + "\n").encode(), stdout=output)
    with pack.open("rb") as source:
        git(destination, env, "index-pack", "--stdin", stdin=source, stdout=subprocess.PIPE)
    git(destination, env, "update-ref", "HEAD", revision, stdout=subprocess.PIPE)
    git(destination, env, "read-tree", revision, stdout=subprocess.PIPE)
    tree = git(destination, env, "ls-tree", "-r", "-z", "--full-tree", revision, stdout=subprocess.PIPE).stdout
    expected = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        meta, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = Path(raw_name.decode())
        if mode not in ("100644", "100755") or kind != "blob" or path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("snapshot requires regular committed files")
        expected[path.as_posix()] = oid
    archive = control / "source.tar"
    with archive.open("wb") as output:
        git(destination, env, "archive", "--format=tar", revision, stdout=output)
    seen = set()
    with tarfile.open(archive) as source:
        for member in source:
            if member.isdir():
                continue
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError("unexpected archive member")
            data = source.extractfile(member).read()
            if blob_id(data) != expected[member.name]:
                raise ValueError("archive differs from committed tree")
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            seen.add(member.name)
    if seen != set(expected):
        raise ValueError("archive omitted committed files")
    validate_snapshot(destination, expected)
    return revision, expected, hashlib.sha256(archive.read_bytes()).hexdigest()


def blob_id(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def validate_snapshot(root, expected):
    seen = set()
    for directory, folders, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        if parent == root:
            folders[:] = [name for name in folders if name not in (".git", "build")]
        if any((parent / name).is_symlink() for name in folders):
            raise ValueError("snapshot contains a symlink")
        for name in files:
            path = parent / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file() or relative not in expected:
                raise ValueError("snapshot contains an uncommitted file")
            if blob_id(path.read_bytes()) != expected[relative]:
                raise ValueError("snapshot source changed")
            seen.add(relative)
    if seen != set(expected):
        raise ValueError("snapshot source missing")


def context_digest(root, paths):
    digest = hashlib.sha256()
    for relative in paths:
        digest.update(str(relative).encode() + b"\0" + (root / relative).read_bytes() + b"\0")
    digest.update(b"Dockerfile\0" + (root / "scripts/e2e/Dockerfile").read_bytes())
    return digest.hexdigest()
