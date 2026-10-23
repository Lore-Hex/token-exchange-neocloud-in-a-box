#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a fixed, offline Debian package-input graph; never install or execute it."""

import argparse
from email.parser import Parser
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat
import sys

LOCK = Path(__file__).resolve().parents[1] / "image/inputs/r595-ubuntu2404.json"
LIMIT = 128 << 20
FIELDS = ("Package", "Version", "Architecture", "Filename", "Size", "SHA256",
          "Depends", "Pre-Depends", "Provides", "Conflicts", "Breaks",
          "Essential", "Multi-Arch")
RELATION = re.compile(
    r"([a-z0-9][a-z0-9+.-]+)(?::(any|native|amd64))?"
    r"(?:\s+\((<<|<=|=|>=|>>)\s+([0-9A-Za-z.+:~-]+)\))?")


def require(condition):
    if not condition:
        raise ValueError("package-inputs-rejected")


def regular(path, limit, *, dir_fd=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= limit)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_nlink,
                              s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(identity(before) == identity(after) and len(data) == before.st_size)
        return data
    finally:
        os.close(fd)


def remove_flat_directory(parent, name, original):
    """Remove only an owned flat staging directory, including on Python 3.10.

    Never follow leaf links or descend into unexpected directories. All opens,
    unlinks and the final rmdir stay anchored to checked directory descriptors.
    """
    require(isinstance(name, str) and name not in ("", ".", "..") and "/" not in name
            and stat.S_ISDIR(original.st_mode) and original.st_uid == os.getuid())
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        current = os.fstat(fd)
        require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino)
                and current.st_uid == original.st_uid)
        leaves = []
        with os.scandir(fd) as entries:
            for entry in entries:
                require(len(leaves) < 16 and not entry.is_dir(follow_symlinks=False))
                leaves.append(entry.name)
        for leaf in leaves:
            os.unlink(leaf, dir_fd=fd)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (original.st_dev, original.st_ino))
        os.rmdir(name, dir_fd=parent)
    finally:
        os.close(fd)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def index_bytes(raw, encoding):
    if encoding == "plain":
        return raw
    require(encoding == "xz")
    result = bytearray()
    # Ubuntu's base indexes contain a second empty stream. XZ 1.2.1 section
    # 2 permits concatenation and four-byte-aligned zero stream padding.
    for _ in range(4):
        dec = lzma.LZMADecompressor(memlimit=LIMIT)
        result.extend(dec.decompress(raw, max_length=LIMIT + 1 - len(result)))
        require(dec.eof and len(result) <= LIMIT)
        raw = dec.unused_data
        padding = len(raw) - len(raw.lstrip(b"\0"))
        require(padding <= 4096 and padding % 4 == 0)
        raw = raw[padding:]
        if not raw:
            return bytes(result)
    raise ValueError("package-inputs-rejected")


def paragraphs(data, *, fields=FIELDS):
    """Use the stdlib header parser, rejecting malformed/duplicate Deb822 fields."""
    text = data.decode("utf-8", errors="strict")
    require("\r" not in text and "\x00" not in text)
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        require(len(block) <= 1 << 20)
        row = Parser().parsestr(block + "\n\n", headersonly=True)
        require(not row.defects and not row.get_payload())
        names = [key.lower() for key in row.keys()]
        require(len(names) == len(set(names)))
        require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", key)
                    for key in row.keys()))
        yield {key: " ".join(row.get(key, "").split()) for key in fields}


def version(value):
    require(isinstance(value, str) and len(value) <= 256
            and re.fullmatch(r"(?:[0-9]+:)?[0-9][0-9A-Za-z.+:~-]*", value))
    epoch, rest = value.split(":", 1) if ":" in value else ("0", value)
    require(epoch.isdecimal())
    upstream, revision = rest.rsplit("-", 1) if "-" in rest else (rest, "0")
    require(upstream and upstream[0].isdigit() and revision
            and re.fullmatch(r"[0-9A-Za-z.+~]+", revision))
    return int(epoch), upstream, revision


def part_compare(a, b):
    # Debian Policy 5.6.12: tilde, end, letters, then other ASCII characters;
    # compare digit runs numerically, ignoring leading zeroes.
    def order(c):
        if c == "~":
            return -1
        if not c or c.isdigit():
            return 0
        return ord(c) if c.isalpha() else ord(c) + 256

    while a or b:
        while (a and not a[0].isdigit()) or (b and not b[0].isdigit()):
            x, y = order(a[:1]), order(b[:1])
            if x != y:
                return (x > y) - (x < y)
            a, b = a[1:], b[1:]
        x = re.match(r"[0-9]*", a)[0]
        y = re.match(r"[0-9]*", b)[0]
        nx, ny = x.lstrip("0"), y.lstrip("0")
        if (len(nx), nx) != (len(ny), ny):
            return ((len(nx), nx) > (len(ny), ny)) * 2 - 1
        a, b = a[len(x):], b[len(y):]
    return 0


def compare(a, b):
    ea, ua, ra = version(a)
    eb, ub, rb = version(b)
    return (ea > eb) - (ea < eb) or part_compare(ua, ub) or part_compare(ra, rb)


def relationships(value, *, provides=False, alternatives=True):
    if not value:
        return []
    require(len(value) <= 65536)
    result = []
    groups = value.split(",")
    require(len(groups) <= 256)
    for group in groups:
        choices = []
        items = group.split("|")
        require(len(items) <= 8)
        for item in items:
            match = RELATION.fullmatch(item.strip())
            require(match is not None)
            name, qualifier, operator, wanted = match.groups()
            if wanted:
                version(wanted)
            if provides:
                require(qualifier is None and operator in (None, "="))
            choices.append((name, qualifier, operator, wanted))
        require(not (provides or not alternatives) or len(choices) == 1)
        result.append(choices)
    return result


def meets(actual, operator, wanted):
    if not operator:
        return True
    if actual is None:
        return False
    c = compare(actual, wanted)
    return {"<<": c < 0, "<=": c <= 0, "=": c == 0,
            ">=": c >= 0, ">>": c > 0}[operator]


def matches(row, relation):
    name, qualifier, operator, wanted = relation
    if qualifier == "any" and row["Multi-Arch"] != "allowed":
        return False
    if name == row["Package"] and meets(row["Version"], operator, wanted):
        return True
    return any(pname == name and meets(pversion, operator, wanted)
               for group in relationships(row["Provides"], provides=True)
               for pname, _, _, pversion in group)


def check_graph(rows, roots, essential):
    require(0 < len(rows) <= 512)
    by_name = {row["Package"]: row for row in rows}
    require(len(by_name) == len(rows) and set(roots) <= by_name.keys()
            and set(essential) <= by_name.keys())
    for row in rows:
        require(row["Architecture"] in ("amd64", "all"))
        version(row["Version"])
        for field in ("Depends", "Pre-Depends"):
            for choices in relationships(row[field]):
                require(any(matches(candidate, choice)
                            for choice in choices for candidate in rows))
        for field in ("Conflicts", "Breaks"):
            for choices in relationships(row[field], alternatives=False):
                # Debian permits a package to conflict with its own virtual
                # Provide, but never with another selected provider.
                require(not any(matches(candidate, choice)
                                for choice in choices for candidate in rows
                                if candidate["Package"] != row["Package"]))
    reached = set(roots) | set(essential)
    while True:
        before = set(reached)
        for name in sorted(before):
            for field in ("Depends", "Pre-Depends"):
                for choices in relationships(by_name[name][field]):
                    reached.update(candidate["Package"] for candidate in rows
                                   if any(matches(candidate, c) for c in choices))
        if reached == before:
            break
    require(reached == by_name.keys())
    return sum(int(row["Size"]) for row in rows)


def check(lock, directory, *, scope="kernel-driver-build-dependency-coverage"):
    require(scope in ("kernel-driver-build-dependency-coverage", "apt-tools-dependency-coverage",
                      "mkosi-build-tools-dependency-coverage", "guest-runtime-dependency-coverage"))
    require(lock["format"] == "nib-package-inputs/v1"
            and lock["scope"] == scope
            and lock["installable_verified"] is False
            and lock["deployable"] is False)
    expected = lock["packages"]
    require(0 < len(expected) <= 512 and 0 < len(lock["indexes"]) <= 8)
    require(len({i["id"] for i in lock["indexes"]}) == len(lock["indexes"]))
    wanted = {(row["index"], row["Package"], row["Version"], row["Architecture"]): row
              for row in expected}
    require(len(wanted) == len(expected))
    observed, essential = {}, set()
    for index in lock["indexes"]:
        require(re.fullmatch(r"[a-z0-9][a-z0-9.-]*", index["file"]) is not None)
        raw = regular(directory / index["file"], 32 << 20)
        require(len(raw) == index["bytes"] and digest(raw) == index["sha256"])
        raw = index_bytes(raw, index["encoding"])
        count = 0
        for row in paragraphs(raw):
            count += 1
            require(count <= 200000)
            if row["Architecture"] not in ("amd64", "all"):
                continue
            if row["Essential"] == "yes":
                essential.add(row["Package"])
            key = index["id"], row["Package"], row["Version"], row["Architecture"]
            if key in wanted:
                require(key not in observed and row == {
                    k: v for k, v in wanted[key].items() if k != "index"})
                observed[key] = row
    require(set(observed) == set(wanted) and sorted(essential) == lock["essential"])
    total = check_graph(list(observed.values()), lock["roots"], lock["essential"])
    require(total == lock["archive_bytes"])
    return {"result": "pass", "check": lock["scope"], "packages": len(observed),
            "archive_bytes": total, "installable_verified": False,
            "deployable": False, "hardware_validated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True,
                        help="directory containing the seven exact public index files")
    args = parser.parse_args()
    raw = regular(LOCK, 2 << 20)
    result = check(json.loads(raw), args.metadata)
    result["lock_sha256"] = digest(raw)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, lzma.LZMAError):
        sys.exit("Package input verification rejected; nothing was installed.")
