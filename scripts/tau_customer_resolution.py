# SPDX-License-Identifier: Apache-2.0
"""Natural task-2 dialogue ending at one lookup; never an evaluator or reward."""
import json
import time

import tau_cpu_identity as identity
import tau_selected_policy as policy
import tau_trial_worker as worker

TOOLS = {"find_user_id_by_email": "email", "find_user_id_by_name_zip": "name_zip"}


def expected_customer(root):
    raw = worker.read(root / "bench/tau-upstream-pins.json", 1 << 20)
    worker.require(worker.digest(raw) == policy.tau.PINS_SHA)
    pins = worker.decode(raw)
    sources = {identity.PREFIX + name: worker.read(root / "upstream" / identity.PREFIX / name, 16 << 20)
               for name in ("tasks.json", "db.json")}
    identity.fixture(sources, pins)
    task = next(row for row in json.loads(sources[identity.PREFIX + "tasks.json"]) if row["id"] == "2")
    ids = {row["arguments"]["user_id"] for row in task["evaluation_criteria"]["actions"]
           if "user_id" in row["arguments"]}
    worker.require(len(ids) == 1)
    expected = ids.pop()
    worker.require(type(expected) is str and 0 < len(expected) <= 256)
    return expected


def dialogue(orchestrator, expected, proxy):
    """Pinned fc0055dc step/termination/tool behavior, no scripted assistant turn."""
    from tau2.data_model.message import ToolCall, ToolMessage
    row = {"schema": policy.RESOLUTION_REPORT_V2, "tool": "none", "outcome": "not_attempted",
           "intended_customer_match": None, "failure_stage": None}
    proxy.customer_resolution = row
    execute = orchestrator._execute_tool_calls
    present, saved = "_execute_tool_calls" in vars(orchestrator), vars(orchestrator).get("_execute_tool_calls")

    def lookup(calls):
        worker.require(row["outcome"] == "not_attempted")
        if type(calls) is not list or len(calls) != 1:
            row["outcome"] = "multiple_tools"
            raise worker.Abort("protocol_error")
        call = calls[0]
        if type(call) is not ToolCall or call.requestor != "assistant" or call.name not in TOOLS:
            row["outcome"] = "unsupported_tool"
            raise worker.Abort("protocol_error")
        row.update(tool=TOOLS[call.name], outcome="lookup_error")
        # Execute the original arguments exactly once through the trusted upstream
        # error boundary. No argument projection, injected identity or repair.
        try:
            results = execute(calls)
        except Exception:
            # Abort/deadline and cancellation derive from BaseException and must
            # retain their terminal status without inventing a lookup cause.
            row["failure_stage"] = "execution_exception"
            raise
        try:
            worker.require(type(results) is list and len(results) == 1 and type(results[0]) is ToolMessage
                           and type(results[0].error) is bool)
            if results[0].error:
                row["failure_stage"] = "upstream_lookup"
            else:
                # Pinned Environment.to_json_str returns retail string IDs unchanged,
                # not JSON-quoted. Compare the original bounded value without repair.
                value = results[0].content
                worker.require(type(value) is str and 0 < len(value) <= 256)
                row.update(outcome="resolved" if value == expected else "wrong_customer",
                           intended_customer_match=value == expected)
        except Exception:
            row["failure_stage"] = "result_validation"
            raise
        return results

    try:
        orchestrator._execute_tool_calls = lookup
        orchestrator._run_start_perf = time.perf_counter()
        orchestrator.initialize()
        for _ in range(policy.LIMITS["turns"] + 1):
            if orchestrator.done:
                break
            worker.require(time.monotonic() < proxy.deadline)
            orchestrator.step()
            orchestrator._check_termination()
            if row["outcome"] != "not_attempted":
                break
        if row["outcome"] != "resolved":
            raise worker.Abort("protocol_error")
        return policy.resolution_result(row, completed=True)
    finally:
        if present:
            orchestrator._execute_tool_calls = saved
        else:
            del orchestrator._execute_tool_calls
        # The pinned text agents' stop hooks do not generate completions. Do not
        # call finalize/run_single_task: those build trajectories or grade tasks.
        failed = False
        for role in ("agent", "user"):
            try:
                getattr(orchestrator, role).stop(None, getattr(orchestrator, role + "_state", None))
            except BaseException:
                failed = True
        if failed:
            raise worker.Abort("infrastructure_error") from None


def run(root, manifest, selection, proxy):
    policy.validate(manifest)
    worker.require(policy.is_resolution(manifest) and selection == policy.support.SELECTION)
    worker.configure_upstream(proxy)
    from tau2.runner.helpers import get_tasks
    from tau2.runner.build import build_text_orchestrator
    from tau2.utils import llm_utils
    task = get_tasks("retail", task_split_name="base", task_ids=["2"], num_tasks=1)[0]
    worker.require(task.initial_state is None and task.user_tools is None and task.evaluation_criteria is not None)
    proxy.assertions = list(task.evaluation_criteria.nl_assertions or [])
    worker.require(len(proxy.assertions) == 1)
    config = worker.text_config(selection, policy.settings(manifest), policy.LIMITS["trial_seconds"])
    try:
        expected = expected_customer(root)
        orchestrator = build_text_orchestrator(config, task, seed=selection["seed"])
        return dialogue(orchestrator, expected, proxy)
    finally:
        llm_utils.set_llm_log_dir(None)
