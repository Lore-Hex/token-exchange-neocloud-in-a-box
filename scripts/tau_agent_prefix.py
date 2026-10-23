# SPDX-License-Identifier: Apache-2.0
"""Original agent policy/tools over a private pipe; never a generated user turn."""

import hashlib
import json
import os
import signal
import subprocess
import time

MODEL = "deepseek-ai/DeepSeek-V4.1-Flash-1017"
MAX_BYTES = 1 << 20
SCHEMA = "nib.tau-private-agent-prefix/v1"


def require(value):
    if not value:
        raise ValueError("agent prefix rejected")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(body):
    require(type(body) is dict and set(body) == {"model", "messages", "tools"}
            and body["model"] == MODEL and type(body["messages"]) is list and len(body["messages"]) == 1
            and type(body["tools"]) is list and 0 < len(body["tools"]) <= 128)
    message = body["messages"][0]
    require(type(message) is dict and set(message) == {"role", "content"}
            and message["role"] == "system" and type(message["content"]) is str and message["content"])
    require(all(type(tool) is dict for tool in body["tools"]) and len(encoded(body)) <= MAX_BYTES)
    return body


def materialize(worker, policy, manifest, selection):
    policy.validate(manifest)
    require(selection == policy.SELECTION)
    class NoGeneration:
        def complete(self, **kwargs):
            raise worker.Abort("protocol_error")
    worker.configure_upstream(NoGeneration())
    from tau2.runner.helpers import get_tasks
    from tau2.runner.build import build_text_orchestrator
    from tau2.data_model.simulation import TextRunConfig
    from tau2.utils.llm_utils import to_litellm_messages
    task = get_tasks("retail", task_split_name="base", task_ids=["2"], num_tasks=1)[0]
    args = {"temperature": 0, "num_retries": 0}
    config = TextRunConfig(
        domain="retail", task_set_name="retail", task_split_name="base", task_ids=["2"], num_tasks=1,
        llm_agent="nib-scripted-agent", llm_args_agent=args, llm_user="nib-scripted-simulator", llm_args_user=args,
        agent="llm_agent", user="user_simulator", num_trials=1, max_concurrency=1, max_steps=2, max_errors=1,
        timeout=min(worker.SECONDS, manifest["limits"]["trial_seconds"]),
        max_retries=0, hallucination_retries=0, auto_review=False, auto_resume=False,
        verbose_logs=False, seed=selection["seed"], save_to=None)
    orchestrator = build_text_orchestrator(config, task, seed=selection["seed"])
    agent = orchestrator.agent
    state = agent.get_init_state()
    require(state.messages == [] and len(state.system_messages) == 1)
    # Same original llm_utils.generate conversion and Tool.openai_schema; do
    # not initialize/step the orchestrator or append a fabricated user turn.
    return validate({"model": MODEL, "messages": to_litellm_messages(state.system_messages),
                     "tools": [tool.openai_schema for tool in agent.tools]})


def receive(channel, plan_sha, deadline):
    packet = channel.receive(deadline)
    require(type(packet) is dict and set(packet) == {"schema", "plan_sha256", "body"}
            and packet["schema"] == SCHEMA and packet["plan_sha256"] == plan_sha)
    validate(packet["body"])
    channel.send({"received_sha256": digest(encoded(packet)), "received": True}, deadline)
    return packet["body"]


def send(channel, body, plan_sha, deadline):
    packet = {"schema": SCHEMA, "plan_sha256": plan_sha, "body": validate(body)}
    channel.send(packet, deadline)
    acknowledgement = channel.receive(deadline)
    require(type(acknowledgement) is dict and set(acknowledgement) == {"received_sha256", "received"}
            and acknowledgement["received"] is True
            and acknowledgement["received_sha256"] == digest(encoded(packet)))


def attach(command, environment, plan_sha, deadline, transport):
    """Transient body only. No stdout, stderr, exception or body is persisted."""
    process = channel = None
    try:
        process = subprocess.Popen(command, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        channel = transport.Channel(os.dup(process.stdout.fileno()), os.dup(process.stdin.fileno()))
        body = receive(channel, plan_sha, deadline)
        require(process.wait(timeout=max(0.001, min(10, deadline - time.monotonic()))) == 0)
        require(os.read(channel.read_fd, 1) == b"")
        return body
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
        if channel is not None:
            channel.close()
        if process is not None:
            process.stdin.close()
            process.stdout.close()
