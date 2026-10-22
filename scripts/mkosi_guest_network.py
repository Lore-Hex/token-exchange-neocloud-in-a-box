# SPDX-License-Identifier: Apache-2.0
"""Fixed opt-in guest helper composition, always before verity generation."""
from contextlib import contextmanager
import csv
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import time

SCRIPT = "scripts/mkosi_guest_network.py"
ROOTFS = Path("/work/output/rootfs")
PAYLOAD = Path("/nib")
SYSUSERS = Path("/usr/bin/systemd-sysusers")
SYSUSERS_SIZE = 68224
SYSUSERS_SHA256 = "f2f07333c751d6a05206e6c5b65df05f7d50d98293d6356d4a6b3c8b4f91301a"
EPOCH = 1792454400


def require(value):
    if not value:
        raise ValueError("guest-network-composition-rejected")


def identity(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def compile_helpers(go, directory, sources, source, base, export):
    """Audited stdlib/local-guest sources; observed, not authenticated compiler."""
    export.source_identity(source)
    require(source.get("profile") == export.NETWORK_PROFILE
            and source["sources"] == {p: base.digest(raw) for p, raw in sources.items()}
            and go.is_absolute() and ".." not in go.parts)
    compiler = base.read(go, 64 << 20)
    directory.mkdir(mode=0o700)
    inputs = export.network_sources(sources)
    base.publish(directory / "source", inputs)
    for name in ("cache", "modules", "tmp"):
        (directory / name).mkdir(mode=0o700)
    env = {"PATH": str(go.parent) + ":/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
           "GOENV": "off", "GOWORK": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off",
           "GOFLAGS": "-mod=readonly", "GOOS": "linux", "GOARCH": "amd64", "GOAMD64": "v1", "CGO_ENABLED": "0",
           "GOCACHE": str(directory / "cache"), "GOMODCACHE": str(directory / "modules"), "TMPDIR": str(directory / "tmp")}
    deadline = time.monotonic() + 120
    def call(args):
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        result = base.processes.execute_process_group([str(go), *args], env=env,
            cwd=directory / "source", timeout=remaining, max_output_bytes=65536,
            max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
        result.check_returncode()
        require(not result.stderr and time.monotonic() < deadline)
        return result.stdout
    require(call(["env", "GOVERSION"]) == b"go1.27.2\n")
    prefix = b"github.com/Lore-Hex/token-exchange-neocloud-in-a-box/guest/"
    payload = {"guest-network.py": sources[SCRIPT]}
    for role in export.NETWORK_ROLES:
        package = "./guest/cmd/nib-" + role
        imports = call(["list", "-deps", "-f", "{{if not .Standard}}{{.ImportPath}}{{end}}", package])
        require(imports.split() == [prefix + b"nftables", prefix + b"internal/service", prefix + b"cmd/nib-" + role.encode()])
        path = directory / ("nib-" + role)
        require(call(["build", "-trimpath", "-buildvcs=false", "-ldflags=-buildid=", "-o", str(path), package]) == b"")
        raw = base.read(path, 8 << 20)
        require(raw[:7] == b"\x7fELF\x02\x01\x01")
        payload["network/nib-" + role] = raw
    require(base.read(go, 64 << 20) == compiler)
    base.verify(directory / "source", inputs)
    for source_name in export.NETWORK_DATA.values():
        payload["network/" + source_name] = sources[source_name]
    receipt = {"schema": "nib.guest-network-inputs/v1", "source": source,
        "compiler": {"version": "go1.27.2", "compiler_sha256": base.digest(compiler),
                     "compiler_authenticated": False, "source_hashes": {p: base.digest(v) for p, v in inputs.items()}},
        "files": {name: identity(payload[member]) for name, member in export.NETWORK_HELPERS.items()}}
    receipt["files"].update({name: identity(sources[member]) for name, member in export.NETWORK_DATA.items()})
    payload["network-inputs.json"] = export.encoded(receipt)
    export.network_inputs(receipt, source)
    return payload, receipt


@contextmanager
def parent(root_fd, name, export, *, create=False):
    require(name in set(export.NETWORK_HELPERS) | set(export.NETWORK_DATA)
            | set(export.NETWORK_LINKS) | set(export.NETWORK_DATABASES))
    current = os.dup(root_fd)
    try:
        for part in name.split("/")[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
            info = os.fstat(current)
            require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o022)
        yield current, name.rsplit("/", 1)[1]
    finally:
        os.close(current)


def read(root_fd, name, export):
    limit = 65536 if name in export.NETWORK_DATABASES else 8 << 20 if name in export.NETWORK_HELPERS else 16384
    with parent(root_fd, name, export) as (directory, leaf):
        fd = os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.getuid()
                    and not stat.S_IMODE(before.st_mode) & 0o022 and 0 < before.st_size <= limit)
            raw = stream.read(limit + 1)
            require(len(raw) == before.st_size and export.stamp(before) == export.stamp(os.fstat(stream.fileno()))
                    and export.stamp(before) == export.stamp(os.stat(leaf, dir_fd=directory, follow_symlinks=False)))
            return raw


def database(raw, width):
    require(len(raw) <= 65536 and raw.endswith(b"\n"))
    rows = list(csv.reader(io.StringIO(raw.decode("ascii")), delimiter=":", quoting=csv.QUOTE_NONE, strict=True))
    require(all(len(row) == width and row[0] and row[0][0] not in "+-" for row in rows)
            and len({row[0] for row in rows}) == len(rows))
    return {row[0]: row for row in rows}


def compose(processes, export, directory, source, prefix, root=ROOTFS, payload=PAYLOAD):
    require(root == ROOTFS and payload == PAYLOAD)
    with directory(payload) as payload_fd:
        fd = os.open("network-inputs.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=payload_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.getuid()
                    and not stat.S_IMODE(info.st_mode) & 0o022 and 0 < info.st_size <= export.MAX_JSON)
            raw = stream.read(export.MAX_JSON + 1)
            require(len(raw) == info.st_size and export.stamp(info) == export.stamp(os.fstat(stream.fileno()))
                    and export.stamp(info) == export.stamp(os.stat("network-inputs.json", dir_fd=payload_fd, follow_symlinks=False)))
    inputs = export.parsed(raw)
    export.network_inputs(inputs, source)
    deadline = time.monotonic() + 120
    with directory(root) as root_fd:
        original = os.fstat(root_fd)
        require(original.st_uid == os.getuid() and not stat.S_IMODE(original.st_mode) & 0o022)
        # Refuse every collision before writing any image file.
        for name in (*export.NETWORK_HELPERS, *export.NETWORK_DATA, *export.NETWORK_LINKS):
            with parent(root_fd, name, export, create=True) as (fd, leaf):
                try:
                    os.stat(leaf, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ValueError("guest-network-image-collision")
        before = {name: database(read(root_fd, name, export), width) for name, width in export.NETWORK_DATABASES.items()}
        require(all("nib" not in rows for rows in before.values()))
        for name, row in inputs["files"].items():
            member = export.NETWORK_HELPERS[name] if name in export.NETWORK_HELPERS else "network/" + export.NETWORK_DATA[name]
            export.copy(payload / member, root / name, row, 8 << 20 if name in export.NETWORK_HELPERS else 16384,
                        deadline, directory)
            with parent(root_fd, name, export) as (fd, leaf):
                output = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    os.fchmod(output, 0o755 if name in export.NETWORK_HELPERS else 0o644)
                    os.fsync(output)
                finally:
                    os.close(output)
            require(identity(read(root_fd, name, export)) == row)
        require(read(root_fd, "etc/nib/network.json", export) == b'{"schema":"nib.guest-network/v1","egress":[]}\n')
        require(read(root_fd, "etc/sysusers.d/nib.conf", export)
                == b'u nib - "Neocloud services" /nonexistent /usr/sbin/nologin\n')
        # The tools tree and libraries are already authenticated by preparation.
        with directory(SYSUSERS.parent) as fd:
            tool = os.open(SYSUSERS.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(tool, "rb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == SYSUSERS_SIZE
                        and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o755)
                binary = stream.read(SYSUSERS_SIZE + 1)
                require(identity(binary) == {"bytes": SYSUSERS_SIZE, "sha256": SYSUSERS_SHA256}
                        and export.stamp(info) == export.stamp(os.fstat(stream.fileno()))
                        and export.stamp(info) == export.stamp(os.stat(SYSUSERS.name, dir_fd=fd, follow_symlinks=False)))
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        # systemd v255.4 fileio.c:search_and_open_internal does not prefix
        # absolute config paths with --root. Account databases do use --root.
        result = processes.execute_process_group([*prefix, str(SYSUSERS), "--root=" + str(root),
            str(root / "etc/sysusers.d/nib.conf")],
            env={"PATH": "/usr/bin:/usr/sbin", "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C",
                 "SOURCE_DATE_EPOCH": str(EPOCH)}, timeout=min(30, remaining),
            max_output_bytes=65536, max_stderr_bytes=65536, stdin=subprocess.DEVNULL)
        result.check_returncode()
        after_raw = {name: read(root_fd, name, export) for name in export.NETWORK_DATABASES}
        after = {name: database(after_raw[name], width) for name, width in export.NETWORK_DATABASES.items()}
        require(all(set(after[name]) == set(before[name]) | {"nib"}
                    and {k: v for k, v in after[name].items() if k != "nib"} == before[name] for name in export.NETWORK_DATABASES))
        user, group = after["etc/passwd"]["nib"], after["etc/group"]["nib"]
        require(user[1] == "x" and user[4:] == ["Neocloud services", "/nonexistent", "/usr/sbin/nologin"]
                and user[2].isascii() and user[2].isdecimal() and user[3].isascii() and user[3].isdecimal()
                and str(int(user[2])) == user[2] and str(int(user[3])) == user[3]
                and 0 < int(user[2]) < 65534 and 0 < int(user[3]) < 65534
                and group[1] == "x" and group[2] == user[3] and group[3] == ""
                and all(row[2] != user[2] for row in before["etc/passwd"].values())
                and all(row[2] != group[2] for row in before["etc/group"].values()))
        shadow, gshadow = after["etc/shadow"]["nib"], after["etc/gshadow"]["nib"]
        require(shadow[1].startswith(("!", "*")) and shadow[2] == str(EPOCH // 86400)
                and shadow[3:] == [""] * 6 and gshadow[1].startswith(("!", "*")) and gshadow[2:] == ["", ""])
        for name, target in export.NETWORK_LINKS.items():
            with parent(root_fd, name, export) as (fd, leaf):
                os.symlink(target, leaf, dir_fd=fd)
                require(os.readlink(leaf, dir_fd=fd) == target)
                os.fsync(fd)
        require((os.stat(root, follow_symlinks=False).st_dev, os.stat(root, follow_symlinks=False).st_ino)
                == (original.st_dev, original.st_ino) and time.monotonic() < deadline)
    receipt = {"schema": "nib.guest-network-composition/v1", "result": "pass", "inputs": inputs,
            "account": {"name": "nib", "uid": int(user[2]), "gid": int(user[3]), "login_disabled": True},
            "databases": {name: identity(raw) for name, raw in after_raw.items()}, "links": export.NETWORK_LINKS,
            "sysusers_stdout_bytes": len(result.stdout), "sysusers_stderr_bytes": len(result.stderr),
            "services_started": False, "network_verified": False, "confidentiality_verified": False}
    export.network_proof(receipt, source)
    return receipt


if __name__ == "__main__":
    raise SystemExit("Private source-bound image composition only; never apply to a host.")
