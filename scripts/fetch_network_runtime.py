#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch a bounded Ubuntu subset from existing runtime locks; never execute it."""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
    sys.exit("Use python3 -I -S -B scripts/fetch_network_runtime.py")

import argparse
import json
from pathlib import Path
import runpy
import tempfile
import time
import types

ROOT = Path(__file__).absolute().parent.parent
SCRIPT = "scripts/fetch_network_runtime.py"
fetcher = types.SimpleNamespace(**runpy.run_path(str(ROOT / "scripts/fetch_mkosi_tools.py")))
prep, PREP_SOURCE = fetcher.base.helper("network_runtime_fetch", "scripts/prepare_network_runtime.py")
base, require = fetcher.base, fetcher.require


def plan(lock, names):
    rows = prep.selection(lock, names)
    full = fetcher.plan(lock, guest=True, guest_scope=fetcher.tools.TARGETS["guest-runtime"][1])
    indexes = {r["index"] for r in rows}
    keys = {"metadata/" + i["file"] for i in lock["indexes"] if i["id"] in indexes}
    keys.update("repo/pool/" + base.package_name(r) for r in rows)
    items = {(name if name.startswith("metadata/") else "archives/" + full[name][2] + ".deb"): full[name]
             for name in keys}
    require(len(items) <= 38 and sum(r[1] for r in items.values()) <= 96 << 20)
    return rows, items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", action="append", required=True)
    args = parser.parse_args()
    revision, sources = fetcher.committed((SCRIPT, prep.SCRIPT, prep.LOCK))
    require(sources[prep.SCRIPT] == PREP_SOURCE)
    lock = json.loads(sources[prep.LOCK], object_pairs_hook=prep.unique)
    rows, items = plan(lock, args.package)
    report = {"schema": "nib.network-runtime-fetch/v1", "result": "fail", "git_sha": revision,
              "source_hashes": {n: base.digest(v) for n, v in sources.items()},
              "lock_sha256": base.digest(sources[prep.LOCK]), "packages": rows,
              "package_execution": False, "static_graph_closed": False}
    try:
        files = fetcher.fetch_parallel(items, base.processes.execute_process_group,
                                      time.monotonic() + 600, time.monotonic, time.sleep)
        with tempfile.TemporaryDirectory(prefix="nib-network-index-") as temporary:
            base.publish(Path(temporary) / "inputs", files)
            prep.metadata_check(lock, rows, Path(temporary) / "inputs/metadata")
        report["result"] = "pass"
    except fetcher.FetchFailure as error:
        files = {}
        report["failure"] = error.report
    files["receipt.json"] = base.encoded(report)
    base.publish(args.output, files)
    base.verify(args.output, files)
    print(json.dumps({k: report[k] for k in ("result", "git_sha")}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Network input fetch rejected; no package execution authorized.")
