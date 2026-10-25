# SPDX-License-Identifier: Apache-2.0
"""Closed task-2 identity follow-up. No response text belongs in a report."""

import hashlib
import json
import re

SCHEMA = "nib.tau-cpu-identity-probe-input/v1"
REPORT = "nib.tau-cpu-identity-probe/v1"
STEPS = ("initial", "email", "name_zip")
QUESTIONS = ("What is the email address associated with your account?",
             "What are your first name, last name, and ZIP code?")
FIELDS = ("email_like_literal", "first_name_literal", "last_name_literal", "zip_literal")
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")
PREFIX = "data/tau2/domains/retail/"


def require(value):
    if not value:
        raise ValueError("CPU identity diagnostic rejected")


def email_literal(content):
    # Bound each regex input; an unbroken non-email string must not cause a
    # quadratic unanchored search over a response-sized buffer.
    return any(EMAIL.search(item.group()) is not None for item in re.finditer(r"\S+", content)
               if len(item.group()) <= 320)


def validate_fixture(value):
    require(type(value) is dict and set(value) == {"first_name", "last_name", "zip"})
    require(all(type(part) is str and 0 < len(part) <= 128 and part.isascii()
                and not any(ord(c) < 32 for c in part) for part in value.values()))
    require(re.fullmatch(r"[0-9]{5}", value["zip"]) is not None)
    return value


def fixture(sources, pins):
    """Verify source bytes before deriving transient facts; never copy the DB email."""
    rows = {}
    for name in ("tasks.json", "db.json"):
        path = PREFIX + name
        raw, expected = sources[path], pins["files"][path]
        require(len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"])
        rows[name] = json.loads(raw)
    tasks = [item for item in rows["tasks.json"] if item["id"] == "2"]
    require(len(tasks) == 1)
    task = tasks[0]
    require(task.get("initial_state") is None and task.get("user_tools") is None)
    scenario = task["user_scenario"]
    require(scenario["persona"] is None and not email_literal(json.dumps(scenario)))
    instructions = scenario["instructions"]
    unknown = instructions["unknown_info"].lower()
    require("email" in unknown and "not remember" in unknown)
    actions = task["evaluation_criteria"]["actions"]
    require(actions[0]["name"] == "find_user_id_by_name_zip")
    value = validate_fixture(actions[0]["arguments"])
    require(all(part.lower() in instructions["known_info"].lower() for part in value.values()))
    users = [user for user in rows["db.json"]["users"].values()
             if user["name"]["first_name"].lower() == value["first_name"].lower()
             and user["name"]["last_name"].lower() == value["last_name"].lower()
             and user["address"]["zip"] == value["zip"]]
    require(len(users) == 1 and all(action["arguments"].get("user_id", users[0]["user_id"])
            == users[0]["user_id"] for action in actions))
    return dict(value)


def project(content, expected):
    """Literal observations only, not semantic agreement, attribution or qualification."""
    validate_fixture(expected)
    require(type(content) is str and 0 < len(content) <= 1 << 20)
    folded = content.lower()
    result = {"email_like_literal": email_literal(content)}
    for name in ("first_name", "last_name", "zip"):
        result[name + "_literal"] = re.search(r"(?<!\w)" + re.escape(expected[name].lower()) + r"(?!\w)", folded) is not None
    return result


def run(orchestrator):
    # fc0055dc UserSimulator.generate_next_message preserves original rendering,
    # role flipping, history and parsing. No candidate/evaluator/tool is called.
    from tau2.data_model.message import AssistantMessage
    orchestrator.initialize()
    user, state = orchestrator.user, orchestrator.user_state
    messages = (orchestrator.message, *(AssistantMessage(role="assistant", content=text) for text in QUESTIONS))
    for message in messages:
        reply, state = user.generate_next_message(message, state)
        reply.validate()
        require(not reply.is_tool_call() and not user.is_stop(reply))
    # The existing completion-only pipe requires a finite float. This sentinel
    # is not an evaluator reward and is never projected into the probe report.
    return 0.0
