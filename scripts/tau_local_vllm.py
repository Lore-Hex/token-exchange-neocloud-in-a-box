# SPDX-License-Identifier: Apache-2.0
"""Local, non-thinking tau transport smoke. Not a scored or attested runner."""

import http.client
import math
import socket
import threading
import time

import tau_transport as transport

tau, worker, require = transport.tau, transport.worker, transport.require
REVISION = "5df63a7203cdb9328d824a27b682e77c7b9928be"
MAX_HTTP_RESPONSE = 2 << 20


def require_nonthinking_auto(body):
    # Only the reviewed text/auto path is supported. Required/named/none-with-tools
    # need separate parser review, even when an engine accepts those values.
    require(body["reasoning_effort"] == "none"
            and (body.get("tool_choice") == "auto" if body.get("tools")
                 else body.get("tool_choice") in (None, "none")))


def tokenize_body(body):
    # At REVISION both chat and tokenize use OnlineRenderer.preprocess_chat.
    require_nonthinking_auto(body)
    result = {"model": body["model"], "messages": body["messages"],
              "add_special_tokens": False, "add_generation_prompt": True,
              "continue_final_message": False, "return_token_strs": False,
              "chat_template_kwargs": {"reasoning_effort": "none", "enable_thinking": False}}
    if body.get("tools"):
        result["tools"] = body["tools"]
    return result


class LocalChatHTTP:
    """Shared bounded chat HTTP; subclasses declare their fixed endpoint paths.

    This does not identify or attest the listening process. The operator must
    verify the local runtime and its framing separately before any real run.
    No credentials, arbitrary hostnames, paths, retries or remote APIs accepted.
    """
    PATHS = ()

    def __init__(self, port, *, clock=time.monotonic):
        require(type(port) is int and 1024 <= port <= 65535)
        self.port, self.clock = port, clock
        self.busy = threading.Lock()

    def remaining(self, deadline):
        require(type(deadline) in (int, float) and math.isfinite(deadline))
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise worker.Abort("timeout")
        require(remaining <= transport.MAX_WAIT_SECONDS)
        return remaining

    def connect(self, sock, deadline):
        sock.connect(("127.0.0.1", self.port))

    def post(self, path, body, deadline):
        require(path in self.PATHS)
        require(threading.current_thread() is threading.main_thread())
        require(self.busy.acquire(blocking=False))
        connection, response, sock = None, None, None
        try:
            raw = worker.bounded_json(body, worker.MAX_REQUEST)
            # A numeric AF_INET socket avoids getaddrinfo and ambient proxy state.
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.set_inheritable(False)
            sock.settimeout(self.remaining(deadline))
            # SIGALRM enforces the whole operation, including slow header/chunk
            # reads. Socket timeouts alone would restart on each incoming byte.
            with worker.alarm(deadline, self.clock):
                self.connect(sock, deadline)
                connection = http.client.HTTPConnection("127.0.0.1", self.port)
                connection.sock = sock
                sock.settimeout(self.remaining(deadline))
                connection.request("POST", path, body=raw, headers={
                    "Content-Type": "application/json", "Accept": "application/json",
                    "Accept-Encoding": "identity", "Connection": "close"})
                response = connection.getresponse()
                if response.status == 429 or 500 <= response.status <= 599:
                    raise worker.Abort("infrastructure_error")
                headers = response.getheaders()
                for name in ("content-type", "content-length", "transfer-encoding", "content-encoding"):
                    require(sum(key.lower() == name for key, _ in headers) <= 1)
                require(response.status == 200
                        and response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                        == "application/json"
                        and response.getheader("Content-Encoding", "identity").lower() == "identity")
                length, encoding = response.getheader("Content-Length"), response.getheader("Transfer-Encoding")
                require(not (length is not None and encoding is not None))
                if length is not None:
                    require(length.isascii() and length.isdecimal() and 0 < int(length) <= MAX_HTTP_RESPONSE)
                require(encoding is None or encoding.lower() == "chunked")
                raw = response.read(MAX_HTTP_RESPONSE + 1)
                self.remaining(deadline)
                require(0 < len(raw) <= MAX_HTTP_RESPONSE
                        and (length is None or len(raw) == int(length)))
                return worker.decode(raw)
        except BaseException as exc:
            reason = ("timeout" if isinstance(exc, TimeoutError) else
                      "infrastructure_error" if isinstance(exc, OSError) else transport.status(exc))
            raise worker.Abort(reason) from None
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()
            if sock is not None:
                sock.close()
            self.busy.release()

    def complete(self, body, prompt_tokens, deadline):
        row = self.post("/v1/chat/completions", body, deadline)
        require(type(row) is dict and "error" not in row and row.get("object") == "chat.completion"
                and row.get("model") == body["model"] and type(row.get("choices")) is list
                and len(row["choices"]) == 1 and type(row.get("usage")) is dict)
        choice, usage = row["choices"][0], row["usage"]
        require(type(choice) is dict and type(choice.get("index")) is int and choice["index"] == 0
                and type(choice.get("message")) is dict)
        message = choice["message"]
        require(message.get("role") == "assistant" and message.get("refusal") in (None, "")
                and set(message) <= {"role", "content", "tool_calls", "refusal", "reasoning",
                                     "reasoning_content", "annotations", "audio", "function_call"}
                and all(message.get(name) in (None, "") for name in ("reasoning", "reasoning_content"))
                and all(message.get(name) is None for name in ("annotations", "audio", "function_call")))
        details = usage.get("completion_tokens_details")
        require(details is None or type(details) is dict
                and (details.get("reasoning_tokens") is None
                     or type(details["reasoning_tokens"]) is int and details["reasoning_tokens"] == 0))
        # Only strip protocol metadata, never repair content, tools or usage.
        selected = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
        if selected.get("tool_calls") is None:
            selected.pop("tool_calls", None)
        require(type(usage.get("prompt_tokens")) is int and usage["prompt_tokens"] == prompt_tokens)
        normalized = {"choices": [{"message": selected, "finish_reason": choice.get("finish_reason")}],
                      "usage": {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}}
        return transport.snapshot(normalized, worker.MAX_RESPONSE)


class LocalVllm(LocalChatHTTP):
    """Pinned vLLM chat tokenization, retaining its original response contract."""
    PATHS = ("/tokenize", "/v1/chat/completions")

    def count(self, body, context_tokens, deadline):
        row = self.post("/tokenize", tokenize_body(body), deadline)
        require(type(row) is dict and {"count", "max_model_len", "tokens"} <= set(row)
                <= {"count", "max_model_len", "tokens", "token_strs"}
                and type(row["count"]) is int and 0 < row["count"] <= context_tokens
                and type(row["max_model_len"]) is int and row["max_model_len"] == context_tokens
                and type(row["tokens"]) is list and len(row["tokens"]) == row["count"]
                and all(type(token) is int and 0 <= token < 2**63 for token in row["tokens"])
                and row.get("token_strs") is None)
        return row["count"]


def trial_roles(role):
    require(type(role) is str and role in ("candidate", "reference"))
    return (role, "simulator", "evaluator")


class LocalVllmSession(transport.ParentSession):
    """Use real local HTTP only through the existing pipe, Bridge and Budget.

    The separate smoke manifest remains quality_eligible=false. Remote model
    APIs, container-worker wiring and runtime-identity proof are not added here.
    """
    def __init__(self, channel, manifest, manifest_sha, role, ports, *, assertions=(),
                 selected_target_only=False, clock=time.monotonic):
        super().__init__(channel, manifest, manifest_sha, role, [], assertions=assertions, clock=clock)
        require(type(selected_target_only) is bool)
        roles = trial_roles(role) if selected_target_only else tau.ROLES
        require(type(ports) is dict and set(ports) == set(roles))
        self.endpoints = {name: LocalVllm(port, clock=clock) for name, port in ports.items()}
        # The model under test cannot also act as its own user or evaluator.
        if selected_target_only:
            require(len(set(ports.values())) == len(roles))
        else:
            require(ports["candidate"] != ports["reference"]
                    and all(ports[name] not in (ports["candidate"], ports["reference"])
                            for name in ("simulator", "evaluator")))

    def prepare_record(self, role, body, deadline):
        # Tokenization is local and non-generating; it must finish within the
        # same per-request ceiling, before reserving any generation spend.
        require(role in self.endpoints)
        deadline = min(deadline, self.bridge.clock() + self.bridge.budget.limits["request_seconds"])
        framing = self.manifest["framing"][role]
        count = self.endpoints[role].count(body, framing["context_tokens"], deadline)
        return {"bounds": {"manifest_sha256": self.manifest_sha, "role": role,
                           "request_sha256": tau.digest(tau.encoded(body)), "prompt_tokens": count,
                           **{key: framing[key] for key in ("framing_sha256", "tokenizer_sha256", "context_tokens")}},
                "role": role, "deadline": deadline}

    def dispatch(self, category, request, bounds):
        require(self.current is not None and self.bridge.budget.pending is not None)
        body, record = self.current
        require(category in transport.ALIASES
                and request == {"messages": body["messages"], "tools": body.get("tools")}
                and bounds["max_tokens"] == body["max_completion_tokens"] and bounds["num_retries"] == 0)
        self.response_deadline = min(self.bridge.budget.pending[4], record["deadline"])
        return self.endpoints[record["role"]].complete(body, record["bounds"]["prompt_tokens"], self.response_deadline)

    def close(self):
        super().close()
        self.endpoints.clear()


if __name__ == "__main__":
    raise SystemExit("Library only. Local transport smoke is not a scored or confidential run.")
