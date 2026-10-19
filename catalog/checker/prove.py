# SPDX-License-Identifier: Apache-2.0
"""Reproducible loopback Tier 1 proof, with a rejecting-catalog control."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata, resources
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from threading import Thread


ROOT = Path(__file__).resolve().parents[2]


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="build/catalog-tier1")
    args = parser.parse_args()
    relative = Path(args.output)
    if relative.is_absolute() or ".." in relative.parts:
        parser.error("output must be a repository-relative directory")
    output = ROOT / relative
    output.mkdir(parents=True, exist_ok=False)
    started = utc()
    raw_log = []
    run = {
        "schema": "nib.evidence/v1", "phase": "P1", "task": "T5",
        "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "command": f"uv run --project catalog/checker --frozen python catalog/checker/prove.py --output {relative.as_posix()}",
        "started_utc": started, "ended_utc": None,
        "host": {"provider": "local", "machine": f"{platform.system()} {platform.release()} {platform.machine()}", "gpus": "none used", "driver": None, "cuda": None},
        "execution": "fake", "hardware_validated": False, "result": "fail",
        "notes": "Synthetic catalog and native discovery only. No model is served. No inference, paid traffic, publication, attestation or hardware validation. Child socket audit guard permits loopback only; public schema lookup uses checker offline fallback. Positive and rejecting-catalog control must both pass their assertions.",
    }
    server = None
    counts = Counter()
    violations = Counter()
    try:
        for target, package in [("nib", "./cmd/nib"), ("nib-catalog", "./cmd/nib-catalog")]:
            built = subprocess.run(["go", "build", "-trimpath", "-o", str(ROOT / "build" / target), package], cwd=ROOT, capture_output=True)
            raw_log.append(built.stdout + built.stderr)
            if built.returncode:
                raise RuntimeError("local build failed")
        generated = subprocess.run([str(ROOT / "build/nib-catalog"), "--root", str(ROOT)], cwd=ROOT, capture_output=True)
        if generated.returncode:
            raw_log.append(generated.stderr)
            raise RuntimeError("catalog generation failed")
        catalog_file = output / "catalog.v2.json"
        catalog_file.write_bytes(generated.stdout)
        if generated.stdout != (ROOT / "catalog/testdata/catalog.v2.golden.json").read_bytes():
            raise RuntimeError("generated catalog differs from committed golden")
        invalid = json.loads(generated.stdout)
        invalid["data"][0]["pricing"]["input"] = "not-a-decimal"
        invalid_file = output / "invalid-catalog.v2.json"
        write_json(invalid_file, invalid)
        files = {
            "/catalog.v2.json": catalog_file,
            "/invalid-catalog.v2.json": invalid_file,
            "/v1/models": ROOT / "catalog/testdata/native-models.json",
        }

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # Never log requests, headers, query strings or bodies.

            def do_GET(self):
                if self.headers.get("Authorization") is not None:
                    violations["authorization_seen"] += 1
                if self.path not in files:
                    violations["unexpected_route"] += 1
                    self.send_error(404)
                    return
                counts[self.path] += 1
                body = files[self.path].read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                violations["post_attempt"] += 1
                self.send_error(405)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        env = {k: v for k, v in os.environ.items() if k != "TR_PROVIDER_API_KEY" and not k.lower().endswith("_proxy")}
        for control, route, expected_exit, status in [
            ("provider-report", "/catalog.v2.json", 0, "pass"),
            ("negative-control-report", "/invalid-catalog.v2.json", 1, "fail"),
        ]:
            report_path = output / f"{control}.json"
            command = [sys.executable, str(ROOT / "catalog/checker/offline_cli.py"), "--base-url", base + "/v1", "--catalog-url", base + route, "--tier", "1", "--json", str(report_path)]
            checked = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, timeout=60)
            raw_log.extend([f"{control}: checker exit {checked.returncode}\n".encode(), checked.stdout, checked.stderr])
            if checked.returncode != expected_exit:
                raise RuntimeError("unexpected checker exit")
            report = json.loads(report_path.read_text())
            checks = {item["id"]: item for item in report["checks"]}
            if len(report["checks"]) != 2 or {k: v["status"] for k, v in checks.items()} != {"catalog.native-model-discovery": "pass", "catalog.declared-v2": status}:
                raise RuntimeError("checker did not produce the required Tier 1 outcomes")
            if checks["catalog.declared-v2"]["measured"]["schema_source"] != "vendored-offline-fallback":
                raise RuntimeError("checker unexpectedly reached a public schema")
            if report["target"]["authentication"]["authorization_header_sent"]:
                raise RuntimeError("authentication must not be used")
        if violations or counts != {"/v1/models": 2, "/catalog.v2.json": 1, "/invalid-catalog.v2.json": 1}:
            raise RuntimeError("unexpected local traffic")
        raw_log.append(b"PASS: local Tier 1 discovery and declaration; rejecting-catalog control; no POST or authenticated requests.\n")
        run["result"] = "pass"
    except Exception as error:
        # Keep failures reproducible without echoing exception payloads.
        raw_log.append(f"FAIL: {type(error).__name__}; inspect local fixture reports.\n".encode())
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        write_json(output / "request-counts.json", {"allowed_gets": dict(counts), "violations": dict(violations), "inference_requests": violations.get("post_attempt", 0)})
        checker_schema = resources.files("tr_provider_check").joinpath("data/catalog.v2.schema.json").read_bytes()
        from tr_provider_check.snapshot import contract_version

        write_json(output / "provenance.json", {
            "checker_version": metadata.version("trustedrouter-provider-check"),
            "checker_contract_sha256": contract_version,
            "checker_schema_sha256": hashlib.sha256(checker_schema).hexdigest(),
            "public_schema_sha256": hashlib.sha256((ROOT / "internal/catalog/catalog.v2.schema.json").read_bytes()).hexdigest(),
            "dependency_lock_sha256": hashlib.sha256((ROOT / "catalog/checker/uv.lock").read_bytes()).hexdigest(),
            "catalog_sha256": hashlib.sha256((output / "catalog.v2.json").read_bytes()).hexdigest() if (output / "catalog.v2.json").exists() else None,
        })
        run["ended_utc"] = utc()
        write_json(output / "run.json", run)
        # Operational output only. The server never emits request data.
        with tempfile.TemporaryDirectory() as temp:
            deny = Path(temp) / "deny.txt"
            deny.write_text(str(ROOT) + "\n" + str(Path.home()) + "\n", encoding="utf-8")
            deny.chmod(0o600)
            scrub = subprocess.run(["sh", "scripts/scrub-secrets.sh", "--deny-file", str(deny)], cwd=ROOT, env={**os.environ, "NIB_BIN": str(ROOT / "build/nib")}, input=b"".join(raw_log), capture_output=True)
            if scrub.returncode:
                run["result"] = "fail"
                run["notes"] += " Operational output scrubbing failed; raw output was not saved."
                write_json(output / "run.json", run)
                print("evidence scrub failed; raw output was not saved", file=sys.stderr)
                return 1
            (output / "output.log").write_bytes(scrub.stdout)
    print(f"Catalog local Tier 1 proof: {run['result']} ({relative.as_posix()})")
    return 0 if run["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
