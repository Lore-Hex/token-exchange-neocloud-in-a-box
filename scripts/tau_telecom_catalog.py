# SPDX-License-Identifier: Apache-2.0
"""Authenticated offline telecom staging; deliberately absent from live owners."""

import time

import tau_external_selection as selection
import tau_runtime as runtime
import tau_telecom_projection as projection

tau, require = runtime.tau, runtime.require
CATALOG = "bench/tau-telecom-catalog.json"
CATALOG_SHA = "3691c557c46c26d6f34771f56a1981c2636846c29e061ec2712f16d9363139fc"
SCHEMA = "nib.tau-telecom-catalog-stage/v1"


def catalog(deadline):
    raw = runtime.read_file(runtime.ROOT / CATALOG, 65536, deadline)
    require(runtime.digest(raw) == CATALOG_SHA)
    return tau.json_value(raw)


def bind(stage, archive_path, deadline):
    """Authenticate original task selection and the reproduced catalog, not spend."""
    require(time.monotonic() < deadline)
    chosen = selection.selected(stage)
    require(chosen["domain"] == "telecom")
    pins_raw = runtime.read_file(runtime.ROOT / tau.PINS, 1 << 20, deadline)
    require(runtime.digest(pins_raw) == tau.PINS_SHA)
    pins = tau.json_value(pins_raw)
    archive = runtime.read_file(archive_path, runtime.ARCHIVE_LIMIT, deadline)
    _, sources = runtime.source_inputs(archive, pins, deadline)
    selection.verify_sources(stage["plan"], sources, pins)
    value = catalog(deadline)
    row = value["selected_tasks"][chosen["task_id"]]
    require(row["tool_catalog_sha256"] == value["tool_catalog_sha256"] and row["tool_count"] == 30)
    return {"schema": SCHEMA, "stage": tau.json_value(tau.encoded(stage)),
            "stage_binding": selection.binding(stage), "catalog_sha256": CATALOG_SHA,
            "tool_catalog_sha256": value["tool_catalog_sha256"], "tool_count": 30,
            "upstream_revision": runtime.REVISION, "source_archive_sha256": runtime.ARCHIVE_SHA,
            "source_tree": runtime.SOURCE_TREE, "runtime_lock_sha256": runtime.CATALOG_SHA,
            "source_authenticated": True, "token_count_parity_verified": False,
            "live_admission": False, "execution_authorized": False, "quality_eligible": False}


def validate(contract, archive_path, deadline):
    require(type(contract) is dict and "stage" in contract)
    expected = bind(contract["stage"], archive_path, deadline)
    require(tau.encoded(contract) == tau.encoded(expected))
    return expected


def project(body, contract, archive_path, deadline):
    """Private in-memory payloads. Never log this return value or input content."""
    bound = validate(contract, archive_path, deadline)
    result = projection.project(body, catalog(deadline)["tools"])
    require(time.monotonic() < deadline and result["tool_catalog_sha256"] == bound["tool_catalog_sha256"])
    result.update(source_authenticated=True, catalog_binding_sha256=tau.digest(tau.encoded(bound)))
    return result
