#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch only the committed public QEMU runtime inputs; never execute them."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/fetch_debug_boot_runtime.py")

import argparse
import json
from pathlib import Path
import re
import runpy
import time
import types

ROOT = Path(__file__).absolute().parent.parent
fetcher = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/fetch_mkosi_tools.py")))
runtime, _ = fetcher.base.helper("debug_runtime_fetch", "scripts/prepare_debug_boot_runtime.py")
base, require = fetcher.base, fetcher.require
SCRIPT = "scripts/fetch_debug_boot_runtime.py"


def plan(lock):
    require(lock["snapshot"] == runtime.SNAPSHOT and lock["roots"] == runtime.ROOTS
            and len(lock["indexes"]) == 4 and 0 < len(lock["packages"]) <= 128)
    items = {}
    for row in lock["indexes"]:
        require(re.fullmatch(r"jammy(?:-updates)?-(?:main|universe)", row["id"])
                and row["file"] == row["id"] + ".xz")
        suite, component = row["id"].rsplit("-", 1)
        expected = runtime.SNAPSHOT + "dists/" + suite + "/" + component + "/binary-amd64/Packages.xz"
        require(row["url"] == expected)
        items["metadata/" + row["file"]] = (expected, row["bytes"], row["sha256"])
    require(len(items) == 4)
    for row in lock["packages"]:
        url = fetcher.public_url(runtime.SNAPSHOT + row["Filename"], snapshot=True)
        name = "archives/" + row["SHA256"] + ".deb"
        require(name not in items and re.fullmatch("[0-9a-f]{64}", row["SHA256"]))
        items[name] = (url, int(row["Size"]), row["SHA256"])
    require(sum(size for _, size, _ in items.values()) <= 96 << 20)
    for _, size, digest in items.values():
        require(type(size) is int and 0 < size <= 32 << 20 and re.fullmatch("[0-9a-f]{64}", digest))
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revision, sources = fetcher.tools.committed((SCRIPT, fetcher.SCRIPT, fetcher.PREPARER,
                                                runtime.SCRIPT, runtime.LOCK, *fetcher.prep.mkosi.EXTRA))
    lock = json.loads(sources[runtime.LOCK])
    files = fetcher.fetch_parallel(plan(lock), base.processes.execute_process_group,
                                   time.monotonic() + 600, time.monotonic, time.sleep)
    files["receipt.json"] = base.encoded({"schema": "nib.debug-runtime-fetch/v1", "result": "pass",
        "git_sha": revision, "lock_sha256": base.digest(sources[runtime.LOCK]), "package_execution": False})
    base.publish(args.output, files)
    runtime.metadata_check(lock, args.output / "metadata")
    base.verify(args.output, files)
    print("NIB_DEBUG_RUNTIME_FETCH_OK")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Debug runtime fetch rejected; no runtime execution occurred.")
