#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bounded credential-free startup observation of one explicitly pinned TLS peer."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    if not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        sys.exit("Use python3 -I -S -B scripts/wait-selected-service.py")
    sys.path.insert(0, str(ROOT / "scripts"))

from contextlib import ExitStack
from datetime import datetime, timezone
import errno
import hashlib
import http.client
import importlib.util
import signal
import ssl
import threading
import time

_spec = importlib.util.spec_from_file_location("selected_external_probe", ROOT / "scripts/probe-selected-service.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)
inference, service = probe.inference, probe.service
require, encoded = probe.require, probe.encoded
SECONDS, ATTEMPTS, ATTEMPT_SECONDS = 1200, 128, 5
BACKOFF = (1, 2, 4, 8, 10)
TRANSIENT = frozenset((errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH,
                       errno.ENETUNREACH, errno.ETIMEDOUT))
SCRIPT = "scripts/wait-selected-service.py"


def source_identity(deadline):
    revision, hashes = probe.source_identity(deadline)
    raw = inference.process.bootstrap.read_source(str(ROOT / SCRIPT))
    digest = hashlib.sha256(raw).hexdigest()
    service.identity.source_receipt({"git_sha": revision, "script": SCRIPT,
                                    "source_sha256": digest}, deadline)
    return revision, {**hashes, SCRIPT: digest}


def empty_result():
    return {"schema": "nib.selected-external-startup/v1", "result": "fail", "failure": "contract",
        "started_at": probe.now(), "ended_at": None, "git_sha": None, "source_sha256": {},
        "deadline": None, "target_sha256": None, "attempts": 0, "tls_checks": 0,
        "health_requests": 0, "health_rejections": 0, "transport_failures": 0,
        "attempt_timeouts": 0, "health_retries": 0, "health_ready": False,
        "max_seconds": SECONDS, "max_attempts": ATTEMPTS, "attempt_seconds": ATTEMPT_SECONDS,
        "backoff_seconds": list(BACKOFF), "credential_accessed": False, "model_requests": 0,
        "ownership_verified": False, "continued_readiness": False, "provider_conformance": False,
        "quality_eligible": False, "deployment_eligible": False, "confidential": False, "e2ee": False}


def attempt(args, trust, peer_digest, deadline, result):
    observation = probe.empty_result()
    with service.identity.worker.alarm(deadline):
        stack = ExitStack()
        try:
            connection = stack.enter_context(probe.pinned_tls(args, trust, peer_digest, deadline))
            result["tls_checks"] += 1
            probe.health(connection, args, deadline, observation)
        finally:
            result["health_requests"] += int(observation["attempts"]["health"])
            try:
                stack.close()
            except BaseException:
                # A failed socket finalization must never become a retry or PASS.
                raise inference.Rejected("internal") from None


def run(args):
    result = empty_result()
    try:
        require(args.run and threading.current_thread() is threading.main_thread(), "contract")
        probe.validate_target(args)
        wall = datetime.strptime(args.deadline, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        seconds = wall - time.time()
        require(0 < seconds <= SECONDS, "deadline")
        hard = time.monotonic() + seconds
        started = time.monotonic()
        prior = signal.getitimer(signal.ITIMER_REAL)
        require(prior[1] == 0, "contract")
        if prior[0]:
            hard = min(hard, started + prior[0])
        result["deadline"] = args.deadline
        result["target_sha256"] = hashlib.sha256(encoded({
            "address": args.address, "port": args.port, "hostname": args.hostname,
            "certificate_sha256": args.certificate_sha256})).hexdigest()

        def cutoff():
            nonlocal hard
            # Same irreversible wall-jump shortening as the selected owner.
            hard = min(hard, time.monotonic() + wall - time.time())
            require(time.monotonic() < hard, "deadline")
            return hard

        with inference.cancellation(threading.Event()), service.identity.worker.alarm(cutoff()):
            result["git_sha"], result["source_sha256"] = source_identity(cutoff())
            trust, peer_digest = probe.trust_input(args, cutoff())
            for index in range(ATTEMPTS):
                deadline = min(cutoff(), time.monotonic() + ATTEMPT_SECONDS)
                result["attempts"] += 1
                result["health_retries"] = index
                verified = result["tls_checks"]
                try:
                    attempt(args, trust, peer_digest, deadline, result)
                except inference.Rejected as error:
                    if error.category != "not-ready":
                        raise
                    result["health_rejections"] += 1
                except (TimeoutError, service.identity.worker.Abort):
                    cutoff()
                    result["attempt_timeouts"] += 1
                except ssl.SSLError:
                    raise  # Includes permanent hostname/chain failures; never retry TLS errors.
                except OSError as error:
                    # Only pre-verification startup transport errors are retryable.
                    if result["tls_checks"] != verified or error.errno not in TRANSIENT:
                        raise
                    result["transport_failures"] += 1
                else:
                    cutoff()
                    result["health_ready"] = True
                    break
                cutoff()
                if index + 1 < ATTEMPTS:
                    wake = time.monotonic() + BACKOFF[min(index, len(BACKOFF) - 1)]
                    while True:
                        left = min(wake, cutoff()) - time.monotonic()
                        if left <= 0:
                            break
                        time.sleep(min(1, left))
            cutoff()
        if result["health_ready"]:
            result.update(result="pass", failure="none")
        else:
            result["failure"] = "attempt-limit"
    except inference.Rejected as error:
        result["failure"] = error.category
    except service.identity.worker.Abort:
        result["failure"] = "deadline"
    except ssl.SSLCertVerificationError:
        result["failure"] = "identity"
    except ssl.SSLError:
        result["failure"] = "transport"
    except (OSError, http.client.HTTPException):
        result["failure"] = "transport"
    except KeyboardInterrupt:
        result["failure"] = "cancelled"
    except BaseException:
        result["failure"] = "internal"
    result["ended_at"] = probe.now()
    return result


def main(argv=None):
    result = empty_result()
    try:
        parser = probe.QuietParser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--run", action="store_true")
        for name in ("address", "hostname", "certificate", "certificate-sha256", "deadline"):
            parser.add_argument("--" + name, required=True)
        parser.add_argument("--port", type=int, required=True)
        result = run(parser.parse_args(argv))
    except BaseException:
        pass
    print(encoded(result).decode())
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
