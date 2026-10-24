# SPDX-License-Identifier: Apache-2.0
"""Selected operational session over independently admitted owner channels."""

import time

import tau_local_vllm as local
import tau_owner_channel as owners
import tau_selected_policy as policy

tau, require = local.tau, local.require


class Session(local.CountedSession):
    """No runtime admission or launcher here; callers must prove both owners."""
    def __init__(self, channel, manifest, manifest_sha, candidate, support, *, assertions, clock=time.monotonic):
        require(type(assertions) in (list, tuple) and len(assertions) == 1
                and type(assertions[0]) is str and 0 < len(assertions[0]) <= 16384)
        require(type(candidate) is owners.Coordinator and type(support) is owners.Coordinator
                and candidate is not support and candidate.channel is not support.channel
                and candidate.channel is not channel and support.channel is not channel)
        manifest = policy.transport_manifest(local.transport.snapshot(manifest, local.worker.MAX_REQUEST), manifest_sha)
        for name, owner in (("candidate", candidate), ("support", support)):
            require(owner.binding == manifest["owners"][name] and owner.sequence == 0
                    and owner.pending is None and owner.failed is None)
            owner.check(owner.deadline)
        super().__init__(channel, manifest, manifest_sha, "candidate", [], assertions=assertions,
                         clock=clock, selected_operational=True)
        self.owners = (candidate, support)
        self.endpoints = {"candidate": candidate.client("candidate"),
                          **{role: support.client(role) for role in ("simulator", "evaluator")}}
        self.bridge.budget.trial_deadline = min(self.bridge.budget.trial_deadline,
                                               candidate.deadline, support.deadline)

    def accept_result(self, packet, deadline):
        sequence, result = super().accept_result(packet, deadline)
        result.update(schema="nib.tau-selected-operational-trial/v1", pricing="not-measured",
                      selection=dict(policy.support.SELECTION), live_admission=False)
        if result["usage"] is not None:
            result["usage"]["cost_usd"] = None
        return sequence, result

    def close(self):
        super().close()
        for owner in self.owners:
            owner.close()
