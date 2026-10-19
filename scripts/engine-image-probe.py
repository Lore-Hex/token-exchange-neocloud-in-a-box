#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Internal read-only payload. No image admission, application or engine import."""

import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
    sys.exit("Isolated read-only payload required.")

import hashlib
import json
import os
from pathlib import Path
import stat
import types


def read(name):
    fd = os.open(Path(__file__).parent / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 2 << 20:
            raise ValueError("payload source rejected")
        data = stream.read((2 << 20) + 1)
        after = os.fstat(stream.fileno())
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise ValueError("payload source changed")
        return data


def main():
    contract_data = read("contract.json")
    if len(sys.argv) != 2 or hashlib.sha256(contract_data).hexdigest() != sys.argv[1]:
        raise ValueError("payload contract rejected")
    contract = json.loads(contract_data)
    modules = []
    for name in ("engine_overlay.py", "engine_image_diagnostic.py"):
        data = read(name)
        if hashlib.sha256(data).hexdigest() != contract["payload_sources"][name]:
            raise ValueError("payload source differs")
        module = types.ModuleType(name)
        module.__file__ = str(Path(__file__).parent / name)
        exec(compile(data, module.__file__, "exec"), module.__dict__)
        modules.append(module)
    overlay, diagnostic = modules
    report = diagnostic.collect(overlay, contract)
    data = overlay.json_bytes(report)
    overlay.require(len(data) <= 2 << 20)
    sys.stdout.buffer.write(data)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit("Read-only image diagnostic failed; no qualification.")
