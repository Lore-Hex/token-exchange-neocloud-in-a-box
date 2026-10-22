#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare trusted-builder export metadata, without asserting attestation."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import sys

import mkosi_export as export
import prepare_guest_packages as files

NAMES = ("manifest.json", "validation.json", "run.json", "result.json", "resources.json")
QUALIFICATIONS = ("signed", "boot_verified", "reproducibility_verified", "confidentiality_verified")
VALIDATOR = "host/validate-native-boot-export.py"
WORKFLOW = ".github/workflows/mkosi-native-build.yml"
require = export.require


def timestamp(value):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value))
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def validate(raws):
    """Reuse the native export proof gates, binding every retained receipt."""
    require(type(raws) is dict and set(raws) == set(NAMES))
    rows = {name: export.parsed(raw) for name, raw in raws.items()}
    manifest, validation, run = (rows[name] for name in NAMES[:3])
    source = manifest["source"]
    staged = export.validate_proof(rows["result.json"], source)
    export.validate_resources(rows["resources.json"])
    receipts = {name: {"bytes": len(raws[name]), "sha256": files.digest(raws[name])}
                for name in NAMES[2:]}
    require(manifest == {"schema": "nib.private-boot-bundle/v1", "source": source,
        "root_hash": staged["root_hash"], "files": staged["files"], "receipts": receipts,
        "local_only": True, **{key: False for key in QUALIFICATIONS}})
    require(manifest["local_only"] is True
            and all(manifest[key] is False for key in QUALIFICATIONS))
    validator = validation["validator_source"]
    require(type(validator) is dict and set(validator) == {VALIDATOR, WORKFLOW}
            and all(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                    for value in validator.values()))
    require(validation == {"schema": "nib.native-private-export-validation/v1", "result": "pass",
        "source": source, "validator_source": validator, "build_result": "success",
        "artifacts_verified": True, "private_images_removed": True, "metadata_only": True,
        "root_hash": staged["root_hash"], "files": staged["files"],
        **{key: False for key in QUALIFICATIONS}})
    require(all(validation[key] is True for key in
                ("artifacts_verified", "private_images_removed", "metadata_only"))
            and all(validation[key] is False for key in QUALIFICATIONS))
    require(run["schema"] == "nib.evidence/v1" and run["result"] == "pass"
            and run["git_sha"] == source["git_sha"] and run["execution"] == "software"
            and run["hardware_validated"] is False and run["phase"] == "P3" and run["task"] == "T1"
            and timestamp(run["started_utc"]) < timestamp(run["ended_utc"]))
    return rows


def read(directory):
    with files.directory(directory) as fd:
        require(set(os.listdir(fd)) == set(NAMES))
        return {name: files.read_at(fd, name, export.MAX_JSON) for name in NAMES}


def compare(left_raws, right_raws, *, expected_source=None):
    left, right = validate(left_raws), validate(right_raws)
    a, b = left["manifest.json"], right["manifest.json"]
    require(a["source"] == b["source"]
            and left["validation.json"]["validator_source"] == right["validation.json"]["validator_source"])
    if expected_source is not None:
        require(type(expected_source) is str and re.fullmatch(r"[0-9a-f]{40}", expected_source)
                and a["source"]["git_sha"] == expected_source)
    # A copied receipt is not a second build. Independence of the actual hosts
    # still requires reviewing the CI job identities outside this offline tool.
    arun, brun = left["run.json"], right["run.json"]
    require(timestamp(arun["ended_utc"]) <= timestamp(brun["started_utc"])
            or timestamp(brun["ended_utc"]) <= timestamp(arun["started_utc"]))
    matches = {name: a["files"][name] == b["files"][name] for name in export.FILES}
    matches["verity_root"] = a["root_hash"] == b["root_hash"]
    return {"schema": "nib.native-export-comparison/v1",
        "result": "pass" if all(matches.values()) else "fail",
        "scope": "trusted-builder-receipt-comparison", "source": a["source"], "matches": matches,
        "inputs": [{name: files.digest(raws[name]) for name in NAMES} for raws in (left_raws, right_raws)],
        "independent_builders_verified": False, "boot_verified": False,
        "signed": False, "confidentiality_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--expected-source", help="Require this exact producer commit, for CI source binding")
    args = parser.parse_args()
    report = compare(read(args.left.absolute()), read(args.right.absolute()), expected_source=args.expected_source)
    sys.stdout.buffer.write(export.encoded(report))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError):
        sys.exit("Native export comparison rejected; no qualification granted.")
