# SPDX-License-Identifier: Apache-2.0
"""Run the existing tau orchestrator through a completion-only pipe proxy.

The caller must verify and isolate the upstream runtime first. This library
neither imports upstream at module scope nor launches a process or endpoint.
"""

import math
import time

import tau_transport as transport

worker, require = transport.worker, transport.require


def execute(root, manifest, selection, proxy, *, run=worker.run_upstream, clock=time.monotonic):
    """Use the unchanged evaluator; only the parent can reserve or settle usage."""
    require(type(proxy) is transport.WorkerProxy and not proxy.finished and not proxy.failed)
    outcome, reward = "infrastructure_error", None
    try:
        with worker.alarm(proxy.deadline, clock):
            value = run(root, manifest, selection, proxy)
            require(type(value) is float and math.isfinite(value) and -1000 <= value <= 1000)
            require(not proxy.failed and not proxy.finished)
            reward, outcome = value, "evaluated"
    except worker.Abort as exc:
        outcome = exc.status
    except KeyboardInterrupt:
        outcome = "cancelled"
    except BaseException:
        pass
    # Transport failures are already authoritative in the parent. A closed
    # channel cannot be reused to turn that failure into a successful trial.
    proxy.finish(outcome, reward)


if __name__ == "__main__":
    raise SystemExit("Library only; no unverified upstream execution operation.")
