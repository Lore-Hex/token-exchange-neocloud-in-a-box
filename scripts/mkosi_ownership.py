# SPDX-License-Identifier: Apache-2.0
"""Existing subordinate-ID mappings and a private-tmpfs ownership proof."""
import hashlib
import os
from pathlib import Path
import pwd
import stat

COUNT = 65535


def require(value):
    if not value:
        raise ValueError("ownership-check-rejected")


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def trusted_bytes(path, *, executable=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0
                and not before.st_mode & 0o022 and 0 < before.st_size <= 1 << 20)
        if executable:
            require(before.st_mode & stat.S_ISUID and before.st_mode & 0o111)
        data = stream.read((1 << 20) + 1)
        after = os.fstat(stream.fileno())
        require(len(data) == before.st_size and identity(before) == identity(after))
        return data


def subordinate(data, name, uid):
    require(type(data) is bytes and 0 < len(data) <= 1 << 20)
    rows = []
    for line in data.decode("ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        require(len(fields) == 3 and fields[1].isdigit() and fields[2].isdigit())
        start, count = map(int, fields[1:])
        require(start > 0 and count > 0 and start + count < (1 << 32) - 1)
        rows.append((fields[0], start, count))
    matches = [(start, count) for owner, start, count in rows if owner in (name, str(uid))]
    require(len(matches) == 1)
    start, count = matches[0]
    require(start >= 65536 and count >= COUNT and not start <= uid < start + count)
    for owner, other, length in rows:
        if owner not in (name, str(uid)):
            require(start + COUNT <= other or other + length <= start)
    return start


def host_inputs():
    uid, gid = os.getuid(), os.getgid()
    require(uid > 0 and gid > 0 and uid == os.geteuid() and gid == os.getegid())
    name = pwd.getpwuid(uid).pw_name
    require(name and ":" not in name and "\n" not in name)
    hashes = {}
    starts = []
    for kind in ("uid", "gid"):
        path = "/etc/sub" + kind
        data = trusted_bytes(path)
        # subgid entries are owned by the login user, not the primary group.
        starts.append(subordinate(data, name, uid))
        helper = "/usr/bin/new" + kind + "map"
        hashes[helper] = hashlib.sha256(trusted_bytes(helper, executable=True)).hexdigest()
    require(not starts[0] <= uid < starts[0] + COUNT
            and not starts[1] <= gid < starts[1] + COUNT)
    return {"uid": uid, "gid": gid, "subuid": starts[0], "subgid": starts[1]}, hashes


def prefix(root, expected):
    library = root / "usr/lib/x86_64-linux-gnu"
    # util-linux 2.39.3 accepts inner:outer:count and invokes the existing host
    # newuidmap/newgidmap helpers. No package install or sub-ID allocation here.
    return [str(library / "ld-linux-x86-64.so.2"), "--inhibit-cache",
            "--library-path", str(library), str(root / "usr/bin/unshare"),
            "--user", "--map-root-user", "--map-users=1:" + str(expected["subuid"]) + ":65535",
            "--map-groups=1:" + str(expected["subgid"]) + ":65535", "--"]


def mapping(data, own, subordinate_start):
    require(type(data) is str and 0 < len(data) < 4096)
    rows = []
    for line in data.splitlines():
        values = line.split()
        require(len(values) == 3 and all(value.isascii() and value.isdecimal() for value in values))
        rows.append(tuple(map(int, values)))
    require(sorted(rows) == [(0, own, 1), (1, subordinate_start, COUNT)])
    require(own > 0 and subordinate_start >= 65536 and not subordinate_start <= own < subordinate_start + COUNT)


def check(expected):
    require(type(expected) is dict and set(expected) == {"uid", "gid", "subuid", "subgid"}
            and all(type(value) is int for value in expected.values())
            and os.geteuid() == os.getegid() == 0)
    for kind in ("uid", "gid"):
        mapping(Path("/proc/self/" + kind + "_map").read_text(encoding="ascii"),
                expected[kind], expected["sub" + kind])
    # Only private tmpfs is touched. Independently vary UID/GID and return to
    # namespace root; do not rely on a chown-suppression shim or host identity.
    path = Path("/tmp/nib-ownership-proof")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        for uid, gid in ((1, 42), (42, 1), (65534, 65535), (65535, 65534), (0, 0)):
            os.fchown(fd, uid, gid)
            info = os.fstat(fd)
            require((info.st_uid, info.st_gid) == (uid, gid) and info.st_nlink == 1)
    finally:
        os.close(fd)
        path.unlink()
